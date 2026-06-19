"""Provision and locate the external Abogen runtime + ffmpeg.

The app does NOT embed Abogen (CUDA/MPS PyTorch + Kokoro models are multi-GB).
Instead it manages an isolated Python environment, installing the latest Abogen
from GitHub with `uv`, pinned to a known-good commit.

ffmpeg is reused from the user's install if present (Settings path / PATH / on
macOS Homebrew), else auto-downloaded as a pinned, static, checksum-verified build
into the app's config dir — see `ensure_ffmpeg()`.
"""
from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional

from .paths import ffmpeg_cache_dir

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

ABOGEN_REPO = "https://github.com/denizsafak/abogen"
# Known-good upstream commit the app pins to (2026-04-30). "Update Abogen" re-pins to main.
DEFAULT_PIN = "9fa81fbe1e00c5da734caf60bd02b849b464caab"

LogFn = Callable[[str], None]


def _log(on_log: Optional[LogFn], msg: str) -> None:
    if on_log:
        on_log(msg)


def find_uv() -> Optional[str]:
    found = shutil.which("uv")
    if found:
        return found
    candidate = Path.home() / ".local" / "bin" / ("uv.exe" if os.name == "nt" else "uv")
    return str(candidate) if candidate.is_file() else None


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
    """Best available compute backend for Kokoro TTS: 'cuda' | 'mps' | 'cpu'.

    Apple Silicon → 'mps' (PyTorch Metal). NVIDIA → 'cuda'. Otherwise 'cpu'.
    This (not has_nvidia_gpu) is what drives the torch wheel choice, so a Mac
    installs the MPS-capable wheel instead of CPU-only.
    """
    if sys.platform == "darwin" and platform.machine() == "arm64":
        return "mps"
    if has_nvidia_gpu():
        return "cuda"
    return "cpu"


def accelerator_label() -> str:
    return {
        "cuda": "NVIDIA GPU (CUDA)",
        "mps": "Apple Silicon GPU (Metal / MPS)",
        "cpu": "CPU only (no GPU acceleration detected)",
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
    accelerator: CUDA/MPS via ``--torch-backend auto`` (uv resolves the right wheel
    per platform — the macOS wheel is MPS-capable), CPU-only via ``cpu``.
    """
    uv = find_uv()
    if not uv:
        raise RuntimeError("uv is not installed. Install it from https://docs.astral.sh/uv/ first.")

    runtime = Path(runtime_dir)
    runtime.mkdir(parents=True, exist_ok=True)
    venv = runtime / "venv"
    py = env_python(runtime_dir)

    if not py.is_file():
        _log(on_log, "Creating Python 3.12 environment…")
        if _stream([uv, "venv", "--python", "3.12", str(venv)], on_log) != 0:
            raise RuntimeError("Failed to create the Python environment.")

    if use_gpu is None:
        use_gpu = detect_accelerator() != "cpu"
    accel = detect_accelerator() if use_gpu else "cpu"
    backend = "cpu" if accel == "cpu" else "auto"
    _log(on_log, f"Compute backend: {accel} ({accelerator_label()}). "
                 f"Installing Abogen from GitHub with torch wheel '{backend}' (large)…")

    ref = pin or "main"
    spec = f"abogen @ git+{ABOGEN_REPO}@{ref}"
    env = dict(os.environ)
    env["UV_TORCH_BACKEND"] = backend

    code = _stream([uv, "pip", "install", "--python", str(py),
                    "--torch-backend", backend, spec], on_log, env=env)
    if code != 0 and accel != "cpu":
        _log(on_log, f"{accel.upper()} install failed; retrying with CPU PyTorch…")
        env["UV_TORCH_BACKEND"] = "cpu"
        code = _stream([uv, "pip", "install", "--python", str(py),
                        "--torch-backend", "cpu", spec], on_log, env=env)
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
# GUI apps on macOS don't inherit the shell PATH, so look in the common brew dirs.
_MAC_EXTRA_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")

# Pinned static builds. Bump the version/url/sha256 here to update (or use the
# "Set up ffmpeg" button, which re-downloads). Checksums are verified before use.
# Both are .zip — extracted with the stdlib zipfile (no extra dependency).
#   Windows : gyan.dev "essentials" (GPL static, includes NVENC + x264/x265).
#   macOS   : osxexperts arm64 static (VideoToolbox comes from the OS).
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
    "macos-arm64": {
        "version": "8.1 (osxexperts arm64, GPL)",
        "approx_mb": 45,
        # osxexperts' *published* checksums are stale/incorrect; these are the
        # values verified against the actual bytes served (2026-06-19).
        "downloads": [
            {
                "url": "https://www.osxexperts.net/ffmpeg81arm.zip",
                "sha256": "ebb82529562b71170807bbc6b0e7eb4f0b13af8cbb0e085bb9e8f6fe709598ad",
                "type": "zip",
                "extract": {"ffmpeg": "ffmpeg"},
            },
            {
                "url": "https://www.osxexperts.net/ffprobe81arm.zip",
                "sha256": "a6640a77d38a6f0527c5b597e599cb36a3427a6931444ed80bc62542421950a1",
                "type": "zip",
                "extract": {"ffprobe": "ffprobe"},
            },
        ],
    },
}


def _ffmpeg_platform_key() -> Optional[str]:
    if sys.platform == "win32":
        return "win-x64"
    if sys.platform == "darwin" and platform.machine() == "arm64":
        return "macos-arm64"
    return None  # Linux / Intel mac: rely on PATH / the system package manager


def _find_ffmpeg_exe(name: str, preferred: str = "") -> Optional[str]:
    """preferred path -> PATH -> (macOS) Homebrew dirs -> download cache. Else None."""
    if preferred and Path(preferred).is_file():
        return preferred
    found = shutil.which(name)
    if found:
        return found
    if sys.platform == "darwin":
        for d in _MAC_EXTRA_DIRS:
            cand = Path(d) / name
            if cand.is_file():
                return str(cand)
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

    Resolution order: explicit path -> PATH -> (macOS) Homebrew -> download cache.
    On a fresh machine with neither tool present, downloads + checksum-verifies the
    pinned build into the config dir. Returns (ffmpeg, ffprobe); values may be bare
    names already found on PATH. Safe to call from a worker thread.
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
