"""Provision and locate the external Abogen runtime + ffmpeg (Windows).

The app does NOT embed Abogen (CUDA PyTorch + Kokoro models are multi-GB). It
manages an isolated Python environment, installing Abogen from GitHub with `uv`
(auto-downloaded into the app folder if not on PATH), pinned to a known-good commit.

ffmpeg is reused from the user's install if present (Settings path / PATH), else
auto-downloaded as a pinned, static, checksum-verified build. Everything the app
needs lands inside the app's data folder — see `ensure_ffmpeg()` / `ensure_uv()`.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional

from .paths import config_dir, ffmpeg_cache_dir, uv_cache_dir

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

ABOGEN_REPO = "https://github.com/denizsafak/abogen"
# Known-good upstream commit the app pins to (2026-04-30). "Update Abogen" re-pins to main.
DEFAULT_PIN = "9fa81fbe1e00c5da734caf60bd02b849b464caab"

LogFn = Callable[[str], None]


def _log(on_log: Optional[LogFn], msg: str) -> None:
    if on_log:
        on_log(msg)


# Pinned uv, auto-downloaded into the app folder if not already installed.
UV_BUILD = {
    "version": "0.11.23",
    "url": "https://github.com/astral-sh/uv/releases/download/0.11.23/uv-x86_64-pc-windows-msvc.zip",
    "sha256": "02ad29f07e674d68726ba3bb1ff25b335d83515756e2b1a194bb56c3cc30e07c",
}


def _uv_dir() -> Path:
    d = config_dir() / "uv"
    d.mkdir(parents=True, exist_ok=True)
    return d


def find_uv() -> Optional[str]:
    """Locate uv: PATH, the user's ~/.local/bin, or the app's own data/uv cache."""
    found = shutil.which("uv")
    if found:
        return found
    for cand in (Path.home() / ".local" / "bin" / ("uv.exe" if os.name == "nt" else "uv"),
                 _uv_dir() / ("uv" + _EXE)):
        if cand.is_file():
            return str(cand)
    return None


def ensure_uv(on_log: Optional[LogFn] = None) -> Optional[str]:
    """Return a uv executable, downloading a pinned copy into data/uv if needed.

    Keeps uv inside the app folder so nothing has to be pre-installed system-wide.
    """
    found = find_uv()
    if found:
        return found
    if sys.platform != "win32":
        return None  # only the Windows uv build is bundled for auto-download
    _log(on_log, f"Downloading uv {UV_BUILD['version']} (one-time)…")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        arc = Path(td) / "uv.zip"
        _download(UV_BUILD["url"], arc, on_log)
        actual = _sha256(arc)
        if actual.lower() != UV_BUILD["sha256"].lower():
            raise RuntimeError(
                "uv download failed checksum verification:\n"
                f"  expected {UV_BUILD['sha256']}\n  got      {actual}")
        _extract(arc, "zip", {"uv" + _EXE: "uv" + _EXE}, _uv_dir())
    uv = _uv_dir() / ("uv" + _EXE)
    _log(on_log, "uv ready.")
    return str(uv) if uv.is_file() else None


def has_nvidia_gpu() -> bool:
    """Best-effort NVIDIA detection for choosing the CUDA torch backend."""
    if shutil.which("nvidia-smi"):
        try:
            r = subprocess.run(["nvidia-smi"], capture_output=True,
                               creationflags=CREATE_NO_WINDOW, timeout=15)
            return r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False
    return False


def detect_accelerator() -> str:
    """Compute backend for Kokoro TTS: 'cuda' (NVIDIA) or 'cpu'.

    Drives the torch wheel choice during provisioning.
    """
    return "cuda" if has_nvidia_gpu() else "cpu"


def accelerator_label() -> str:
    return {
        "cuda": "NVIDIA GPU (CUDA)",
        "cpu": "CPU only (no NVIDIA GPU detected)",
    }[detect_accelerator()]


def env_python(runtime_dir: str) -> Path:
    """Path to the provisioned environment's python."""
    venv = Path(runtime_dir) / "venv"
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def is_provisioned(runtime_dir: str) -> bool:
    py = env_python(runtime_dir)
    if not py.is_file():
        return False
    try:
        r = subprocess.run([str(py), "-c", "import abogen"], capture_output=True,
                           creationflags=CREATE_NO_WINDOW, timeout=30)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def detect_existing_abogen() -> Optional[str]:
    """Return a Python that already has Abogen installed, if one is obvious.

    Convenience fallback only; the default flow provisions fresh. If you already
    have an Abogen interpreter, point the ``ABOGEN_PYTHON`` environment variable at
    its ``python`` (or set ``abogen_python`` on the Settings tab).
    """
    env_candidate = (os.environ.get("ABOGEN_PYTHON") or "").strip()
    if env_candidate and Path(env_candidate).is_file():
        try:
            r = subprocess.run([env_candidate, "-c", "import abogen"],
                               capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=30)
            if r.returncode == 0:
                return env_candidate
        except (OSError, subprocess.SubprocessError):
            pass
    return None


def _stream(cmd: list[str], on_log: Optional[LogFn], env: Optional[dict] = None) -> int:
    _log(on_log, "$ " + subprocess.list2cmdline(cmd))
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        creationflags=CREATE_NO_WINDOW, env=env,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        _log(on_log, line.rstrip())
    proc.wait()
    return proc.returncode


def provision_abogen(
    runtime_dir: str,
    *,
    pin: str = DEFAULT_PIN,
    use_gpu: Optional[bool] = None,
    on_log: Optional[LogFn] = None,
) -> Path:
    """Create the isolated env and install Abogen from GitHub. Returns env python.

    Heavy (multi-GB on first run). The torch wheel is chosen from the detected
    accelerator: CUDA via ``--torch-backend auto``, else CPU-only via ``cpu``.
    """
    uv = ensure_uv(on_log)
    if not uv:
        raise RuntimeError("Could not find or download uv. Install it from https://docs.astral.sh/uv/ .")

    # Keep uv's cache inside the app data dir, co-located with the venv, so installs
    # hardlink instead of copy and stay self-contained.
    uv_env = dict(os.environ)
    uv_env["UV_CACHE_DIR"] = str(uv_cache_dir())

    runtime = Path(runtime_dir)
    runtime.mkdir(parents=True, exist_ok=True)
    venv = runtime / "venv"
    py = env_python(runtime_dir)

    if not py.is_file():
        _log(on_log, "Creating Python 3.12 environment…")
        if _stream([uv, "venv", "--python", "3.12", str(venv)], on_log, env=uv_env) != 0:
            raise RuntimeError("Failed to create the Python environment.")

    if use_gpu is None:
        use_gpu = detect_accelerator() != "cpu"
    accel = detect_accelerator() if use_gpu else "cpu"
    backend = "cpu" if accel == "cpu" else "auto"
    _log(on_log, f"Compute backend: {accel} ({accelerator_label()}). "
                 f"Installing Abogen from GitHub with torch wheel '{backend}' (large)…")

    ref = pin or "main"
    spec = f"abogen @ git+{ABOGEN_REPO}@{ref}"

    def _install(be: str, copy: bool) -> int:
        cmd = [uv, "pip", "install", "--python", str(py)]
        if copy:
            cmd += ["--link-mode", "copy"]
        cmd += ["--torch-backend", be, spec]
        env = dict(uv_env)
        env["UV_TORCH_BACKEND"] = be
        return _stream(cmd, on_log, env=env)

    # 1) efficient (hardlinked) install with the chosen backend
    code = _install(backend, copy=False)
    # 2) hardlinking can fail on cloud-managed / cross-volume dirs (Windows os error
    #    396) — retry copying files instead, keeping the same (GPU) backend
    if code != 0:
        _log(on_log, "Install failed; retrying with --link-mode=copy "
                     "(works around cloud/hardlink errors)…")
        code = _install(backend, copy=True)
    # 3) last resort: CPU PyTorch
    if code != 0 and accel != "cpu":
        _log(on_log, "Still failing; retrying with CPU PyTorch…")
        code = _install("cpu", copy=True)
    if code != 0:
        raise RuntimeError("Abogen installation failed. See the log for details.")

    if not is_provisioned(runtime_dir):
        raise RuntimeError("Abogen installed but could not be imported.")
    _log(on_log, "Abogen ready.")
    return py


def resolve_abogen_python(cfg: dict) -> Optional[str]:
    """Pick the Abogen interpreter to use: explicit override, provisioned env, else None."""
    explicit = (cfg.get("abogen_python") or "").strip()
    if explicit and Path(explicit).is_file():
        return explicit
    runtime = cfg.get("abogen_runtime_dir", "")
    if runtime and is_provisioned(runtime):
        return str(env_python(runtime))
    return None


# ---------------------------------------------------------------------------
# ffmpeg auto-provisioning (pinned, static, checksum-verified)
# ---------------------------------------------------------------------------
_EXE = ".exe" if os.name == "nt" else ""

# Pinned static build. Bump the version/url/sha256 here to update (or use the
# "Set up ffmpeg" button, which re-downloads). Checksum is verified before use.
# A .zip extracted with the stdlib zipfile (no extra dependency): gyan.dev
# "essentials" (GPL static, includes NVENC + x264/x265).
FFMPEG_BUILDS = {
    "win-x64": {
        "version": "8.0.1 (gyan.dev essentials, GPL)",
        "approx_mb": 106,
        "downloads": [
            {
                "url": "https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-8.0.1-essentials_build.zip",
                "sha256": "e2aaeaa0fdbc397d4794828086424d4aaa2102cef1fb6874f6ffd29c0b88b673",
                "type": "zip",
                "extract": {
                    "ffmpeg.exe": "ffmpeg-8.0.1-essentials_build/bin/ffmpeg.exe",
                    "ffprobe.exe": "ffmpeg-8.0.1-essentials_build/bin/ffprobe.exe",
                },
            },
        ],
    },
}


def _ffmpeg_platform_key() -> Optional[str]:
    return "win-x64" if sys.platform == "win32" else None


def _find_ffmpeg_exe(name: str, preferred: str = "") -> Optional[str]:
    """preferred path -> PATH -> download cache. Else None."""
    if preferred and Path(preferred).is_file():
        return preferred
    found = shutil.which(name)
    if found:
        return found
    cached = ffmpeg_cache_dir() / (name + _EXE)
    return str(cached) if cached.is_file() else None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dest: Path, on_log: Optional[LogFn] = None) -> None:
    _log(on_log, f"Downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "VAbkStudio"})
    with urllib.request.urlopen(req) as r, open(dest, "wb") as f:  # noqa: S310 (pinned URLs)
        total = int(r.headers.get("Content-Length") or 0)
        read = 0
        last = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            read += len(chunk)
            if total and on_log:
                pct = read * 100 // total
                if pct >= last + 10:
                    last = pct
                    _log(on_log, f"  {pct}%  ({read >> 20} / {total >> 20} MB)")


def _extract(archive: Path, kind: str, extract_map: dict, cache: Path) -> None:
    """Extract each {out_name: member_path} from a .zip archive into the cache dir."""
    if kind != "zip":
        raise RuntimeError(f"Unsupported ffmpeg archive type '{kind}' (expected zip).")
    with zipfile.ZipFile(archive) as z:
        names = [n for n in z.namelist()
                 if "__MACOSX" not in n and not Path(n).name.startswith("._")]
        for out_name, member in extract_map.items():
            target = next((n for n in names if n.replace("\\", "/").endswith(member)), None)
            if target is None:
                target = next((n for n in names if Path(n).name == Path(member).name), None)
            if target is None:
                raise RuntimeError(f"{member} not found in {archive.name}")
            with z.open(target) as src, open(cache / out_name, "wb") as out:
                shutil.copyfileobj(src, out)


def ensure_ffmpeg(ffmpeg_pref: str = "", ffprobe_pref: str = "", *,
                  on_log: Optional[LogFn] = None, force: bool = False) -> tuple[str, str]:
    """Resolve ffmpeg & ffprobe, auto-downloading a pinned static build if missing.

    Resolution order: explicit path -> PATH -> download cache (in the app folder).
    On a fresh machine with neither tool present, downloads + checksum-verifies the
    pinned build. Returns (ffmpeg, ffprobe); values may be bare names found on PATH.
    Safe to call from a worker thread.
    """
    if not force:
        ff = _find_ffmpeg_exe("ffmpeg", ffmpeg_pref)
        fp = _find_ffmpeg_exe("ffprobe", ffprobe_pref)
        if ff and fp:
            return ff, fp

    key = _ffmpeg_platform_key()
    build = FFMPEG_BUILDS.get(key) if key else None
    if not build:
        # Unsupported for auto-download (e.g. Linux): best effort with what's around.
        return (_find_ffmpeg_exe("ffmpeg", ffmpeg_pref) or ffmpeg_pref or "ffmpeg",
                _find_ffmpeg_exe("ffprobe", ffprobe_pref) or ffprobe_pref or "ffprobe")

    cache = ffmpeg_cache_dir()
    _log(on_log, f"Setting up ffmpeg {build['version']} "
                 f"(~{build['approx_mb']} MB, one-time download)…")
    for dl in build["downloads"]:
        if not force and all((cache / out).is_file() for out in dl["extract"]):
            continue
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            arc = Path(td) / "ffmpeg_download"
            _download(dl["url"], arc, on_log)
            actual = _sha256(arc)
            if actual.lower() != dl["sha256"].lower():
                raise RuntimeError(
                    "ffmpeg download failed checksum verification:\n"
                    f"  url      {dl['url']}\n"
                    f"  expected {dl['sha256']}\n"
                    f"  got      {actual}")
            _extract(arc, dl["type"], dl["extract"], cache)

    ff = cache / ("ffmpeg" + _EXE)
    fp = cache / ("ffprobe" + _EXE)
    if os.name != "nt":
        for p in (ff, fp):
            try:
                p.chmod(0o755)
            except OSError:
                pass
    _log(on_log, "ffmpeg ready.")
    return str(ff), str(fp)
