# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**VAbk Studio** — a **Windows** PyQt6 app that turns books into "visual audiobooks" (a video of
word-synced karaoke captions over a black canvas while narration plays). It is a thin **GUI
orchestrator**: it does NOT embed Abogen (Kokoro TTS) or ffmpeg — it downloads and drives them. Every
heavy piece (uv, ffmpeg, the Abogen env, models) lands in a `data/` folder beside the app, so it's
self-contained/portable. See `README.md` for the end-user tour, `CONTRIBUTING.md` for verifying changes.

**Status:** experimental, vibecoded for personal use — built fast and shared as-is. Favor pragmatic,
working fixes over ceremony; there's no test/lint suite and stability isn't guaranteed.

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

**Two driver modes** (`spec["mode"]`): `extract` returns the chapter list (for the picker dialog); the
default *generate* accepts `spec["selected_indices"]` and re-synthesizes only the chosen chapters. The
driver calls Abogen's *web* pipeline in-process (`extract_from_path` → `build_pending_job_from_extraction`
→ `ConversionService.enqueue(run_conversion_job)` → poll `job.status`) — that's what Abogen's web
"finish" button does, since it has no real CLI.

**Karaoke highlighting** (`_maybe_install_karaoke`, only when subtitle mode is "Sentence +
Highlighting"): Abogen's web `SubtitleWriter` emits plain `.ass`, so the driver monkeypatches
`kokoro.KPipeline.__call__` (to capture per-token timestamps) and the module-level
`conversion_runner.SubtitleWriter` (to emit `{\kf<cs>}` per word). `get_pipeline` is a closure and
can't be patched — patch the class method + module attribute instead.

## Data layout (paths.py is the keystone) — everything lives in `<app>/data`

`app/paths.py` is **dependency-free** and the single source of truth for filesystem locations — it
breaks the `settings ↔ video_builder` import cycle, so keep it import-light.
- `config_dir()` — `<app_root>/data` by default (override: `VABK_DATA_DIR`). Holds `config.json`,
  `abogen-runtime/` (the provisioned env), `ffmpeg/`, `uv/` (auto-downloaded uv), `hf-cache/` (the
  Kokoro models, via `HF_HOME` set in `abogen_client._driver_env`), and `uv-cache/`. Keeping it all
  **inside the app folder** makes the app portable/self-contained and dodges the cloud-managed user
  profile, where uv hardlinks fail (os error 396). `provision_abogen` sets `UV_CACHE_DIR` here so the
  cache is co-located with the venv (hardlinks work), and still falls back to `--link-mode=copy` if not.
- `app_root()` — project root from source, **exe dir when frozen** (so `data/` sits next to the exe).

**First-run workspace** (`main._maybe_first_run` + `ui/first_run_dialog.py`): on first launch a dialog
picks a base folder (default = `app_root()`) and `settings.derive_workspace()` creates `Input/`,
`Output/`, `Visual Audiobooks/` under it; `workspace_configured` then suppresses the prompt. Under
`--smoke` the dialog is skipped (headless default).

## GPU acceleration

`provisioning.detect_accelerator()` returns `cuda` | `cpu` and drives the torch wheel choice plus the
UI status label (`accelerator_label`, and `abogen_client.gpu_status` which runs Abogen's own
`get_gpu_acceleration` in its interpreter). `provision_abogen` installs the CUDA wheel
(`--torch-backend auto`) when an NVIDIA GPU is detected, else CPU. Video: `video_builder.VIDEO_CODECS`
is NVENC (`hevc/h264/av1_nvenc`) + CPU encoders (x265/x264/aom); `default_video_codec()` → `hevc_nvenc`
(the UI filters by `available_encoders` and the user can pick a CPU encoder if there's no NVENC).

## Auto-downloads: ffmpeg + uv (`provisioning.ensure_ffmpeg` / `ensure_uv`)

Pinned, **SHA-256-verified** `.zip` downloads, extracted with the stdlib `zipfile` (no extra deps),
into `<app>/data`: ffmpeg from gyan.dev essentials (`FFMPEG_BUILDS`, has NVENC + x264/x265), and uv
from its GitHub release (`UV_BUILD`) when not already on PATH. The render/pipeline workers call
`ensure_ffmpeg()` at the start of `run()` (off the UI thread); `provision_abogen` calls `ensure_uv()`.
To update either: bump url + sha256 in one place. **gyan's `.7z` uses BCJ2, which py7zr can't decode —
use the `.zip`.**

## `video_builder.py` (the ffmpeg engine — GUI-agnostic, also a CLI)

Default preset: 1080p/24fps/black/`-shortest`, `libopus 48k mono`, platform hardware encoder. Key
robustness tricks (all deliberate): render to `<name>.part` then atomic `os.replace`; copy the `.ass`
to a temp `sub.ass` and run ffmpeg with `cwd` there (sidesteps Windows subtitle-path escaping); pass an
explicit `-f <mux>`; `set_ass_font_size()` rewrites the `.ass` Style fontsize; encoders auto-detected.

## Tabs & workers

`main.py` builds four tabs: **Full Pipeline** (`ui/pipeline_tab.py`, book→audio→video), **Abogen**
(`ui/abogen_tab.py`, audio-only — *subclasses `PipelineTab`* with `audio_only=True`, reusing its
provisioning/voice/chapter machinery), **FFMPEG** (`ui/video_tab.py`, render existing `.m4b`+`.ass`),
**Settings** (`ui/settings_tab.py`, tool paths + "Set up ffmpeg" + default folders). The Full
Pipeline/Abogen tabs show Abogen + FFmpeg status indicators and one **Download Dependencies** button
(`_download_dependencies`) that provisions whatever's missing.

Long work runs on QThreads in `ui/*_worker.py`, posting results via Qt signals: `PipelineWorker`
(Abogen then ffmpeg per book), `RenderWorker` (ffmpeg only), `ProvisionWorker` (install Abogen),
`FfmpegWorker` (download ffmpeg), `ExtractWorker` (chapter list). The render/pipeline workers parallelize with a `ThreadPoolExecutor`
sized by the "Parallel" spinbox (default 3, no upper cap) and call `ensure_ffmpeg()` at the top of `run()`.
`PipelineWorker` generates into a **temp dir** then moves `.m4b`/`.ass` to the audio folder and `.mp4`
to the output folder, flat (Abogen forces a timestamped subfolder internally — hence temp-then-move via
`_move_into`).

## Commands

```powershell
# run from source (for development; end users just get the .exe)
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
.venv\Scripts\python.exe run.py
```

Verify (no pytest/lint suite):
- Offscreen smoke: `QT_QPA_PLATFORM=offscreen .venv\Scripts\python.exe run.py --smoke` (constructs 4 tabs, exit 0).
- ffmpeg CLI: `python -m app.video_builder encoders` · `... render --audio X.m4b --outdir out --dump-cmd`.
- ffmpeg download: delete `data\ffmpeg\` and start a render (re-fetches + verifies).
- Build the exe: `.\build.ps1` → `dist\VAbkStudio.exe` (spec at `build/VAbkStudio.spec`). Pushing a
  `v*` tag triggers `.github/workflows/release.yml` to build it and attach it to a GitHub Release.

## Rebuild gotchas
- PyInstaller cannot overwrite a **running** `VAbkStudio.exe` — stop it first.
- The driver is bundled as **data**, so changes to `app/abogen_driver.py` require a rebuild to take
  effect in a packaged exe.
- Never stop the app mid-render: it kills child ffmpeg and leaves a truncated `.part` (cleaned up on
  the next run; a finished file only appears after the atomic replace).

## Environment specifics
Windows-only. Pinned upstream Abogen commit lives in `provisioning.DEFAULT_PIN`; pinned ffmpeg/uv in
`FFMPEG_BUILDS` / `UV_BUILD`. The PyInstaller spec excludes torch/abogen/numpy. When frozen,
`app_root()` is the exe's dir, so `data/` (and all downloads) sit next to `VAbkStudio.exe`.
