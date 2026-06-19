# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**VAbk Studio** — a cross-platform (Windows + macOS) PyQt6 app that turns books into "visual
audiobooks" (a video of word-synced karaoke captions over a black canvas while narration plays). It
is a thin **GUI orchestrator**: it does NOT embed Abogen (Kokoro TTS) or ffmpeg — it drives them as
external processes. See `README.md` for the end-user tour, `CONTRIBUTING.md` for how to verify changes.

## The three-process architecture (read this first)

1. **The app** (`app/`, runs in `.venv`) — pure PyQt6 + `requests`. Never imports torch/kokoro/abogen.
2. **Abogen** runs in a **separate Python interpreter** (its own multi-GB env with torch/Kokoro). The
   app finds it via `provisioning.resolve_abogen_python()` (an existing install via the
   `ABOGEN_PYTHON` env var, or one it provisions with `uv` from a pinned GitHub commit).
3. **ffmpeg** is invoked by `video_builder.py`. It is resolved from the Settings path → PATH → an
   auto-downloaded cache (`provisioning.ensure_ffmpeg`, pinned static build into the config dir).

`app/abogen_driver.py` is the bridge: it runs **inside the Abogen interpreter, not `.venv`**. It is
bundled as PyInstaller **data** (not frozen code) so it can be handed to the external python as a
script (`abogen_client.driver_path()` resolves it in both dev and frozen `_MEIPASS`).

**Driver ↔ app protocol:** the driver prints sentinel-tagged JSON lines on stdout —
`@@VAS_EVENT@@{"event": "progress"|"log"|"done"|"error"|"chapters", ...}`. `abogen_client._run_driver`
parses these; everything else on stdout (abogen logging, ffmpeg) is ignored.

## Cross-platform model (paths.py is the keystone)

`app/paths.py` is **dependency-free** and the single source of truth for filesystem locations — it
breaks the `settings ↔ video_builder` import cycle, so keep it import-light.
- `config_dir()` — `%APPDATA%\VAbkStudio` (Win) / `~/Library/Application Support/VAbkStudio` (mac) /
  `~/.config/VAbkStudio` (Linux). Holds `config.json`, `abogen-runtime/`, and `ffmpeg/` (the cache).
- `app_root()` — project root from source, exe dir when frozen.
- All OS-specific code is guarded by `sys.platform` / `os.name`. **Never hard-code a path.**

**First-run workspace** (`main._maybe_first_run` + `ui/first_run_dialog.py`): on first launch a dialog
picks a base folder (default = `app_root()`) and `settings.derive_workspace()` creates `Input/`,
`Output/`, `Visual Audiobooks/` under it; `workspace_configured` then suppresses the prompt. Under
`--smoke` the dialog is skipped (headless default).

## GPU acceleration (real, end-to-end)

`provisioning.detect_accelerator()` returns `cuda` | `mps` | `cpu` and drives **both** the torch wheel
and the UI status label (`accelerator_label`, plus `abogen_client.gpu_status` which runs Abogen's own
`get_gpu_acceleration` in its interpreter).
- **TTS / Apple Silicon:** Abogen's web pipeline already selects `mps` on Darwin+arm (its
  `_select_device`). Two things make it actually work here: `provision_abogen` installs the
  **MPS-capable** wheel on Mac (`--torch-backend auto`, *not* cpu — the old `has_nvidia_gpu` default
  would have forced CPU), and `abogen_client._driver_env` sets `PYTORCH_ENABLE_MPS_FALLBACK=1` (Abogen
  sets this in its `main.py`, which the driver never runs).
- **Video:** `video_builder.VIDEO_CODECS` includes NVENC, **VideoToolbox** (`hevc/h264_videotoolbox`),
  and CPU encoders. `default_video_codec()` resolves per platform (nvenc on Win, videotoolbox on Mac).
  Rate control differs per family — see `quality_flags` (NVENC `-cq`, VideoToolbox `-q:v -allow_sw 1`,
  CPU `-crf`) and `derive_suffix` (`cq`/`q`/`crf`).

## ffmpeg auto-download (`provisioning.ensure_ffmpeg`)

Pinned, static, **SHA-256-verified** `.zip` builds in `FFMPEG_BUILDS` (gyan.dev essentials on Windows,
osxexperts arm64 on macOS — both extracted with the stdlib `zipfile`, no extra deps). Downloads only
when nothing is on PATH; cached in `config_dir()/ffmpeg/`. The render/pipeline workers call it at the
start of `run()` (off the UI thread) so rendering "just works". To update: bump url + sha256 in one
place. **gyan's `.7z` uses BCJ2, which py7zr can't decode — use the `.zip`.**

## `video_builder.py` (the ffmpeg engine — GUI-agnostic, also a CLI)

Default preset: 1080p/24fps/black/`-shortest`, `libopus 48k mono`, platform hardware encoder. Key
robustness tricks (all deliberate): render to `<name>.part` then atomic `os.replace`; copy the `.ass`
to a temp `sub.ass` and run ffmpeg with `cwd` there (sidesteps Windows subtitle-path escaping); pass an
explicit `-f <mux>`; `set_ass_font_size()` rewrites the `.ass` Style fontsize; encoders auto-detected.

## Commands

```bash
# run from source (or use the Start VAbk Studio.bat / .command launchers)
uv venv --python 3.12 .venv
uv pip install --python .venv/Scripts/python.exe -r requirements.txt   # mac/Linux: .venv/bin/python
.venv/Scripts/python.exe run.py
```

Verify (no pytest/lint suite):
- Offscreen smoke: `QT_QPA_PLATFORM=offscreen .venv/.../python run.py --smoke` (constructs 4 tabs, exit 0).
- ffmpeg CLI: `python -m app.video_builder encoders` · `... render --audio X.m4b --outdir out --dump-cmd`.
- ffmpeg download: delete `<config>/VAbkStudio/ffmpeg/` and start a render (re-fetches + verifies).
- Build the optional exe: `.\build.ps1` → `dist\VAbkStudio.exe` (PyInstaller spec at `build/VAbkStudio.spec`).

## Rebuild gotchas
- PyInstaller cannot overwrite a **running** `VAbkStudio.exe` — stop it first.
- The driver is bundled as **data**, so changes to `app/abogen_driver.py` require a rebuild to take
  effect in a packaged exe.
- Never stop the app mid-render: it kills child ffmpeg and leaves a truncated `.part` (cleaned up on
  the next run; a finished file only appears after the atomic replace).

## Environment specifics
Pinned upstream Abogen commit lives in `provisioning.DEFAULT_PIN`. macOS may need `espeak-ng`
(`brew install espeak-ng`) for Kokoro's phonemizer. The PyInstaller spec excludes torch/abogen/numpy.
