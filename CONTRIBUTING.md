# Contributing to VAbk Studio

VAbk Studio is a small PyQt6 GUI that orchestrates **Abogen** (Kokoro TTS) and **ffmpeg**. It is
deliberately a thin layer — most of the heavy logic lives in those external tools. Keep changes
focused and match the surrounding style; there is no build step for development.

## Run from source

```bash
git clone https://github.com/stanyanman/VAbk-Studio.git
cd VAbk-Studio
uv venv --python 3.12 .venv
uv pip install --python .venv/Scripts/python.exe -r requirements.txt   # macOS/Linux: .venv/bin/python
.venv/Scripts/python.exe run.py                                        # macOS/Linux: .venv/bin/python run.py
```

(Or just use the `Start VAbk Studio.bat` / `Start VAbk Studio.command` launchers.)

## Verifying a change (there is no pytest/lint suite)

- **Offscreen smoke test** — constructs all four tabs and exits 0:
  - Windows: `set QT_QPA_PLATFORM=offscreen && .venv\Scripts\python.exe run.py --smoke`
  - macOS/Linux: `QT_QPA_PLATFORM=offscreen .venv/bin/python run.py --smoke`
- **ffmpeg engine CLI**:
  - `python -m app.video_builder encoders` — list detected encoders
  - `python -m app.video_builder render --audio X.m4b --outdir out --dump-cmd` — print the ffmpeg command
  - `python -m app.video_builder scan <folder>` — list audio/subtitle pairs
- **ffmpeg auto-download**: delete the cache (`<config dir>/VAbkStudio/ffmpeg/`) and start a render;
  it should fetch + checksum-verify the pinned build (`provisioning.ensure_ffmpeg`).

## macOS / Apple Silicon checklist

- First run shows the workspace dialog and creates `Input/`, `Output/`, `Visual Audiobooks/`.
- **Set up Abogen** installs the MPS torch wheel (not CPU); the GPU checkbox shows
  *"MPS GPU available and enabled."*
- A short EPUB → `.m4b` + `.ass` with TTS running on MPS (no *"Using CPU"*). If phonemization
  fails, `brew install espeak-ng`.
- `python -m app.video_builder encoders` lists `*_videotoolbox`; a render defaults to
  `hevc_videotoolbox` and plays.

## Conventions / where things live

- `app/paths.py` — config dir, app root, ffmpeg cache. **Keep it dependency-free** (it breaks the
  settings ↔ video_builder import cycle).
- `app/provisioning.py` — uv/Abogen install, GPU detection (`detect_accelerator`), and the pinned
  ffmpeg download. To update ffmpeg, bump `FFMPEG_BUILDS` (url + sha256) — one place.
- `app/video_builder.py` — the ffmpeg engine; encoder metadata + rate control live here.
- Platform-specific code is guarded by `sys.platform` / `os.name` — never hard-code paths.
