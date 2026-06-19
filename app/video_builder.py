"""ffmpeg engine: build the 'visual audiobook' video from audio + .ass subtitles.

GUI-agnostic and CLI-testable. The GUI runs `render()` inside a worker thread and
wires the callbacks to Qt signals; the same `render()` can be driven from the
command line (see `python -m app.video_builder --help`).

Default preset reproduces the user's preferred command exactly:
    1920x1080, 24fps, solid-black canvas, burn .ass via the subtitles filter,
    hevc_nvenc -preset p5 -cq 35 -pix_fmt yuv420p, libopus 48k mono, -shortest
...and fixes the filename bug (`$base_...` -> `${base}_...`) in the original script.

The notorious Windows subtitle-path escaping is sidestepped entirely: the .ass is
copied to `sub.ass` inside a per-render temp dir and ffmpeg is run with cwd there,
so the subtitles filter only ever sees a bare `sub.ass` (no drive colons, spaces,
apostrophes, or commas to escape — works for any book title).
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable, Iterable, Optional

from .paths import ffmpeg_cache_dir

# ---------------------------------------------------------------------------
# Process helpers
# ---------------------------------------------------------------------------
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# Empty by default: find_executable() falls back to PATH (shutil.which). Override the
# exact ffmpeg/ffprobe paths on the Settings tab if they are not on PATH.
DEFAULT_FFMPEG = ""
DEFAULT_FFPROBE = ""

AUDIO_EXTS = (".m4b", ".m4a", ".mp3", ".opus", ".ogg", ".flac", ".wav", ".aac")
SUB_EXTS = (".ass", ".ssa", ".srt")


def find_executable(name: str, preferred: str) -> Optional[str]:
    """Locate ffmpeg/ffprobe: explicit preferred path, then PATH, then download cache.

    The download cache is populated by provisioning.ensure_ffmpeg(); this lets the
    engine find an auto-downloaded build even when nothing is on PATH.
    """
    if preferred and Path(preferred).is_file():
        return preferred
    found = shutil.which(name)
    if found:
        return found
    cached = ffmpeg_cache_dir() / (name + (".exe" if os.name == "nt" else ""))
    return str(cached) if cached.is_file() else None


def _run_capture(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=CREATE_NO_WINDOW,
    )


def available_encoders(ffmpeg: str) -> set[str]:
    """Return the set of encoder names ffmpeg reports (e.g. {'hevc_nvenc', ...})."""
    try:
        out = _run_capture([ffmpeg, "-hide_banner", "-encoders"])
    except OSError:
        return set()
    names: set[str] = set()
    for line in out.stdout.splitlines():
        m = re.match(r"\s*[A-Z.]{6}\s+(\S+)", line)
        if m:
            names.add(m.group(1))
    return names


def probe_duration(path: str, ffprobe: str) -> Optional[float]:
    """Return media duration in seconds, or None if it can't be determined."""
    try:
        out = _run_capture(
            [ffprobe, "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=nokey=1:noprint_wrappers=1", path]
        )
        return float(out.stdout.strip())
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Codec metadata
# ---------------------------------------------------------------------------
# label -> ffmpeg encoder name (filtered against availability at runtime)
VIDEO_CODECS = {
    "HEVC (NVENC, GPU)": "hevc_nvenc",
    "H.264 (NVENC, GPU)": "h264_nvenc",
    "AV1 (NVENC, GPU)": "av1_nvenc",
    "HEVC (VideoToolbox, GPU)": "hevc_videotoolbox",
    "H.264 (VideoToolbox, GPU)": "h264_videotoolbox",
    "HEVC (x265, CPU)": "libx265",
    "H.264 (x264, CPU)": "libx264",
    "AV1 (aom, CPU)": "libaom-av1",
}

NVENC_PRESETS = ["p1", "p2", "p3", "p4", "p5", "p6", "p7"]
X26X_PRESETS = ["ultrafast", "superfast", "veryfast", "faster", "fast",
                "medium", "slow", "slower", "veryslow"]

_CODEC_SHORT = {
    "hevc_nvenc": "hevc", "h264_nvenc": "h264", "av1_nvenc": "av1",
    "hevc_videotoolbox": "hevc", "h264_videotoolbox": "h264",
    "libx265": "x265", "libx264": "x264", "libaom-av1": "av1",
}


def is_nvenc(codec: str) -> bool:
    return codec.endswith("_nvenc")


def is_videotoolbox(codec: str) -> bool:
    return codec.endswith("_videotoolbox")


def default_video_codec() -> str:
    """Best hardware encoder for this platform (the UI filters by availability and
    falls back to a CPU encoder if this one isn't present)."""
    if sys.platform == "darwin":
        return "hevc_videotoolbox"   # Apple Silicon / VideoToolbox
    return "hevc_nvenc"              # Windows/Linux + NVIDIA


def default_video_settings() -> "VideoSettings":
    """VideoSettings with a platform-appropriate default codec + quality."""
    s = VideoSettings()
    s.video_codec = default_video_codec()
    if is_videotoolbox(s.video_codec):
        # VideoToolbox uses -q:v (≈1-100, higher = better), not CQ/CRF. 60 ≈ visually
        # lossless for caption-on-black content.
        s.quality = 60
    return s


def presets_for(codec: str) -> list[str]:
    if is_nvenc(codec):
        return NVENC_PRESETS
    if codec in ("libx264", "libx265"):
        return X26X_PRESETS
    return []  # libaom-av1 has no -preset


def quality_label(codec: str) -> str:
    if is_nvenc(codec):
        return "CQ"
    if is_videotoolbox(codec):
        return "Q"
    return "CRF"


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
@dataclass
class VideoSettings:
    width: int = 1920
    height: int = 1080
    fps: int = 24
    video_codec: str = "hevc_nvenc"
    preset: str = "p5"
    quality: int = 35                 # -cq for NVENC, -crf for CPU encoders
    pix_fmt: str = "yuv420p"
    audio_mode: str = "opus"          # opus | aac | copy
    audio_bitrate: str = "48k"
    audio_channels: int = 1
    background: str = "black"         # black | color | image
    background_color: str = "0x000000"
    background_image: str = ""
    burn_subtitles: bool = True
    subtitle_style_override: str = ""  # libass force_style, e.g. "Fontsize=42"
    carry_chapters: bool = True
    overwrite: bool = True
    container: str = "mp4"            # mp4 | mkv (mkv forced for soft-sub embed)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "VideoSettings":
        valid = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in (d or {}).items() if k in valid})


def quality_flags(s: VideoSettings) -> list[str]:
    if is_nvenc(s.video_codec):
        return ["-cq", str(s.quality)]
    if is_videotoolbox(s.video_codec):
        # VideoToolbox constant quality (-q:v ≈1-100, higher = better). -allow_sw lets
        # it fall back to the software VideoToolbox encoder if HW HEVC is unavailable.
        return ["-q:v", str(s.quality), "-allow_sw", "1"]
    if s.video_codec == "libaom-av1":
        return ["-crf", str(s.quality), "-b:v", "0"]
    return ["-crf", str(s.quality)]


def derive_suffix(s: VideoSettings) -> str:
    """e.g. '1080p_24fps_hevc_cq35_opus48k_p5' — matches the user's naming."""
    parts = [f"{s.height}p", f"{s.fps}fps", _CODEC_SHORT.get(s.video_codec, s.video_codec)]
    qtag = "cq" if is_nvenc(s.video_codec) else ("q" if is_videotoolbox(s.video_codec) else "crf")
    parts.append(qtag + str(s.quality))
    if s.audio_mode == "copy":
        parts.append("audiocopy")
    else:
        parts.append(f"{s.audio_mode}{s.audio_bitrate.rstrip('k')}k")
    if presets_for(s.video_codec):
        parts.append(s.preset)
    return "_".join(parts)


def output_name(base: str, s: VideoSettings) -> str:
    ext = "mkv" if (not s.burn_subtitles or s.container == "mkv") else "mp4"
    return f"{base}_{derive_suffix(s)}.{ext}"


# ---------------------------------------------------------------------------
# Media pairing
# ---------------------------------------------------------------------------
@dataclass
class MediaPair:
    audio: str
    ass: Optional[str]
    base: str

    @property
    def has_subs(self) -> bool:
        return bool(self.ass)


def set_ass_font_size(ass_path, size: int) -> bool:
    """Rewrite the Fontsize of every Style in an .ass file. Returns True if changed.

    Locates the Fontsize column from the `[V4+ Styles]` Format line, so it is robust
    to column ordering. No-op for non-.ass files."""
    p = Path(ass_path)
    if p.suffix.lower() not in (".ass", ".ssa"):
        return False
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    fontsize_col: Optional[int] = None
    in_styles = False
    changed = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        low = stripped.lower()
        if low.startswith("["):
            in_styles = low.startswith("[v4")
            continue
        if not in_styles:
            continue
        if low.startswith("format:"):
            cols = [c.strip().lower() for c in stripped.split(":", 1)[1].split(",")]
            if "fontsize" in cols:
                fontsize_col = cols.index("fontsize")
        elif low.startswith("style:") and fontsize_col is not None:
            head, rest = line.split(":", 1)
            fields = rest.split(",")
            if len(fields) > fontsize_col:
                fields[fontsize_col] = str(int(size))
                lines[i] = head + ":" + ",".join(fields)
                changed = True
    if changed:
        try:
            p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            return False
    return changed


def find_sub_for(audio_path: Path) -> Optional[str]:
    for ext in SUB_EXTS:
        cand = audio_path.with_suffix(ext)
        if cand.is_file():
            return str(cand)
    return None


def scan_folder(folder: str) -> list[MediaPair]:
    """Find audio files in a folder and pair each with a same-basename subtitle."""
    pairs: list[MediaPair] = []
    p = Path(folder)
    if not p.is_dir():
        return pairs
    for entry in sorted(p.iterdir()):
        if entry.is_file() and entry.suffix.lower() in AUDIO_EXTS:
            pairs.append(MediaPair(str(entry), find_sub_for(entry), entry.stem))
    return pairs


def pair_from_audio(audio: str) -> MediaPair:
    ap = Path(audio)
    return MediaPair(audio, find_sub_for(ap), ap.stem)


# ---------------------------------------------------------------------------
# Command building
# ---------------------------------------------------------------------------
def build_command(
    ffmpeg: str,
    audio: str,
    sub_name: Optional[str],
    out_path: str,
    s: VideoSettings,
    *,
    embed_sub_path: Optional[str] = None,
    test_seconds: Optional[float] = None,
) -> list[str]:
    """Build the ffmpeg argv. `sub_name` is the bare subtitle filename relative to
    the cwd ffmpeg will be run in (for burn-in); `embed_sub_path` is an absolute
    path used only when embedding a soft subtitle track (mkv)."""
    args = [ffmpeg, "-hide_banner", "-y" if s.overwrite else "-n",
            "-nostats", "-progress", "pipe:1"]

    # Input 0: video canvas (image loop, or lavfi solid color)
    pre_filter = ""
    if s.background == "image" and s.background_image:
        args += ["-loop", "1", "-framerate", str(s.fps), "-i", s.background_image]
        pre_filter = (
            f"scale={s.width}:{s.height}:force_original_aspect_ratio=decrease,"
            f"pad={s.width}:{s.height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
        )
    else:
        color = "black" if s.background == "black" else s.background_color
        args += ["-f", "lavfi", "-i", f"color=c={color}:s={s.width}x{s.height}:r={s.fps}"]

    # Input 1: audio
    args += ["-i", audio]

    # Input 2 (optional): soft subtitle file to embed
    if embed_sub_path:
        args += ["-i", embed_sub_path]

    # Video filtergraph
    chain = [c for c in (pre_filter,) if c]
    if s.burn_subtitles and sub_name:
        sub = f"subtitles={sub_name}"
        if s.subtitle_style_override:
            sub += f":force_style='{s.subtitle_style_override}'"
        chain.append(sub)
    if chain:
        args += ["-vf", ",".join(chain)]

    # Stream mapping
    args += ["-map", "0:v:0", "-map", "1:a:0"]
    if embed_sub_path:
        args += ["-map", "2:s:0"]

    # Chapters / metadata carried from the audiobook (input 1)
    if s.carry_chapters:
        args += ["-map_metadata", "1", "-map_chapters", "1"]
    else:
        args += ["-map_chapters", "-1"]

    # Video codec
    args += ["-c:v", s.video_codec]
    if presets_for(s.video_codec):
        args += ["-preset", s.preset]
    args += quality_flags(s)
    args += ["-pix_fmt", s.pix_fmt]

    # Audio codec
    if s.audio_mode == "copy":
        args += ["-c:a", "copy"]
    else:
        enc = "libopus" if s.audio_mode == "opus" else "aac"
        args += ["-c:a", enc, "-b:a", s.audio_bitrate, "-ac", str(s.audio_channels)]

    # Soft subtitle codec
    if embed_sub_path:
        args += ["-c:s", "copy"]  # mkv preserves .ass styling

    if test_seconds:
        args += ["-t", str(test_seconds)]

    # Set the muxer explicitly so the output extension can be anything (e.g. a
    # temporary ".part" file) without ffmpeg failing to guess the container.
    muxer = "matroska" if (not s.burn_subtitles or s.container == "mkv") else "mp4"
    args += ["-shortest", "-f", muxer, out_path]
    return args


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
class RenderError(RuntimeError):
    pass


_TIME_RE = re.compile(r"out_time=(\d+):(\d+):(\d+(?:\.\d+)?)")


def _parse_progress_seconds(line: str) -> Optional[float]:
    m = _TIME_RE.search(line)
    if not m:
        return None
    h, mnt, sec = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + float(sec)


def render(
    pair: MediaPair,
    settings: VideoSettings,
    out_dir: str,
    *,
    ffmpeg: str = DEFAULT_FFMPEG,
    ffprobe: str = DEFAULT_FFPROBE,
    on_progress: Optional[Callable[[float], None]] = None,
    on_log: Optional[Callable[[str], None]] = None,
    cancel: Optional[threading.Event] = None,
    test_seconds: Optional[float] = None,
) -> str:
    """Render one pair to a video. Returns the output path. Raises RenderError."""
    ffmpeg = find_executable("ffmpeg", ffmpeg) or ffmpeg
    ffprobe = find_executable("ffprobe", ffprobe) or ffprobe

    def log(msg: str) -> None:
        if on_log:
            on_log(msg)

    # ffmpeg runs with cwd set to a temp dir (so the subtitles filter sees a bare
    # 'sub.ass'); therefore every other path handed to ffmpeg must be absolute.
    audio_abs = str(Path(pair.audio).resolve())
    out_dir_abs = Path(out_dir).resolve()
    out_dir_abs.mkdir(parents=True, exist_ok=True)
    final_path = out_dir_abs / output_name(pair.base, settings)
    settings = VideoSettings.from_dict(settings.to_dict())  # local copy
    if not settings.overwrite and final_path.exists():
        raise RenderError(f"Output already exists: {final_path.name}")
    # Render to a .part file and atomically rename on success, so an in-progress
    # or interrupted render never appears as a finished (but unplayable) output.
    part_path = final_path.with_name(final_path.name + ".part")
    settings.overwrite = True  # always (over)write our own scratch .part
    out_path = str(part_path)
    if settings.background == "image" and settings.background_image:
        settings.background_image = str(Path(settings.background_image).resolve())
    total = probe_duration(audio_abs, ffprobe)
    if test_seconds:
        total = min(total or test_seconds, test_seconds)

    tmp = Path(tempfile.mkdtemp(prefix="vas_"))
    sub_name: Optional[str] = None
    embed_path: Optional[str] = None
    try:
        if pair.ass:
            ass_abs = str(Path(pair.ass).resolve())
            if settings.burn_subtitles:
                sub_name = "sub" + Path(pair.ass).suffix.lower()
                shutil.copyfile(ass_abs, tmp / sub_name)
            else:
                embed_path = ass_abs  # soft-sub track (mkv)

        args = build_command(
            ffmpeg, audio_abs, sub_name, out_path, settings,
            embed_sub_path=embed_path, test_seconds=test_seconds,
        )
        log("CMD: " + subprocess.list2cmdline(args))

        log_path = tmp / "ffmpeg.log"
        with open(log_path, "w", encoding="utf-8", errors="replace") as lf:
            proc = subprocess.Popen(
                args,
                cwd=str(tmp),
                stdout=subprocess.PIPE,
                stderr=lf,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=CREATE_NO_WINDOW,
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                if cancel is not None and cancel.is_set():
                    proc.terminate()
                    raise RenderError("Cancelled by user.")
                secs = _parse_progress_seconds(line)
                if secs is not None and total and total > 0 and on_progress:
                    on_progress(min(100.0, secs / total * 100.0))
            proc.wait()

        tail = ""
        try:
            tail = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
        if proc.returncode != 0:
            log(tail[-4000:])
            raise RenderError(f"ffmpeg exited with code {proc.returncode}")
        log(tail[-1500:])
        if on_progress:
            on_progress(100.0)
        os.replace(part_path, final_path)  # atomic: only now does the final file appear
        return str(final_path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        try:  # remove the scratch file if the render failed/was cancelled
            if part_path.exists():
                part_path.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# CLI (testing / power use)
# ---------------------------------------------------------------------------
def _cli(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="VAbk Studio ffmpeg engine")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sc = sub.add_parser("scan", help="list audio/subtitle pairs in a folder")
    sc.add_argument("folder")

    en = sub.add_parser("encoders", help="list available video encoders")
    en.add_argument("--ffmpeg", default=DEFAULT_FFMPEG)

    rd = sub.add_parser("render", help="render one pair to video")
    rd.add_argument("--audio", required=True)
    rd.add_argument("--ass", default=None)
    rd.add_argument("--outdir", required=True)
    rd.add_argument("--ffmpeg", default=DEFAULT_FFMPEG)
    rd.add_argument("--ffprobe", default=DEFAULT_FFPROBE)
    rd.add_argument("--limit", type=float, default=None, help="render only N seconds (testing)")
    rd.add_argument("--codec", default=None)
    rd.add_argument("--cq", type=int, default=None)
    rd.add_argument("--height", type=int, default=None)
    rd.add_argument("--dump-cmd", action="store_true", help="print command and exit")

    args = ap.parse_args(argv)

    if args.cmd == "scan":
        for pr in scan_folder(args.folder):
            print(f"{'[subs]' if pr.has_subs else '[----]'}  {pr.base}")
            print(f"        audio: {pr.audio}")
            if pr.ass:
                print(f"        subs : {pr.ass}")
        return 0

    if args.cmd == "encoders":
        ff = find_executable("ffmpeg", args.ffmpeg) or args.ffmpeg
        if not ff:
            print("ffmpeg not found (not on PATH, no download cache). "
                  "Run the app once to auto-download it, or pass --ffmpeg.")
            return 1
        for n in sorted(available_encoders(ff)):
            print(n)
        return 0

    if args.cmd == "render":
        s = VideoSettings()
        if args.codec:
            s.video_codec = args.codec
        if args.cq is not None:
            s.quality = args.cq
        if args.height is not None:
            s.height = args.height
            s.width = round(args.height * 16 / 9)
        pair = MediaPair(args.audio, args.ass or find_sub_for(Path(args.audio)),
                         Path(args.audio).stem)
        if args.dump_cmd:
            ff = find_executable("ffmpeg", args.ffmpeg) or args.ffmpeg or "ffmpeg"
            print(subprocess.list2cmdline(
                build_command(ff, pair.audio, "sub.ass",
                              str(Path(args.outdir) / output_name(pair.base, s)), s,
                              test_seconds=args.limit)))
            return 0
        out = render(
            pair, s, args.outdir,
            ffmpeg=args.ffmpeg, ffprobe=args.ffprobe,
            on_progress=lambda p: print(f"\r{p:5.1f}%", end="", flush=True),
            on_log=lambda m: print("\n" + m),
            test_seconds=args.limit,
        )
        print(f"\nWROTE {out}")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(_cli())
