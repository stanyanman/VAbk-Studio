# Contributing to VAbk Studio

VAbk Studio is a small **Windows** PyQt6 GUI that orchestrates **Abogen** (Kokoro TTS) and **ffmpeg**.
It is deliberately a thin layer — most of the heavy logic lives in those external tools. Keep changes
focused and match the surrounding style; there is no build step for development.

## Run from source

```powershell
git clone https://github.com/stanyanman/VAbk-Studio.git
cd VAbk-Studio
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
.venv\Scripts\python.exe run.py
```

(End users don't do this — they just download the portable `VAbkStudio.exe` from Releases.)

## Verifying a change (there is no pytest/lint suite)

- **Offscreen smoke test** — constructs all four tabs and exits 0:
  `set QT_QPA_PLATFORM=offscreen && .venv\Scripts\python.exe run.py --smoke`
- **ffmpeg engine CLI**:
  - `python -m app.video_builder encoders` — list detected encoders
  - `python -m app.video_builder render --audio X.m4b --outdir out --dump-cmd` — print the ffmpeg command
  - `python -m app.video_builder scan <folder>` — list audio/subtitle pairs
- **Auto-downloads**: delete `data\ffmpeg\` (or `data\uv\`) and trigger a render / Abogen setup; the
  app re-fetches + checksum-verifies the pinned build (`provisioning.ensure_ffmpeg` / `ensure_uv`).
- **The exe**: `.\build.ps1` → run `dist\VAbkStudio.exe` (frozen mode puts `data/` next to the exe).

## Conventions / where things live

- Everything the app generates lives in **`<app>/data`** (config, the Abogen env, ffmpeg, uv, the HF
  model cache, uv's cache) so it's portable. Override with `VABK_DATA_DIR`.
- `app/paths.py` — the data dir, app root, ffmpeg/uv caches. **Keep it dependency-free** (it breaks
  the settings ↔ video_builder import cycle).
- `app/provisioning.py` — uv + Abogen install, GPU detection (`detect_accelerator`), and the pinned
  ffmpeg/uv downloads. To update either, bump `FFMPEG_BUILDS` / `UV_BUILD` (url + sha256) — one place.
- `app/video_builder.py` — the ffmpeg engine; encoder metadata + rate control live here.
- Don't hard-code paths — resolve them through `app/paths.py`.
