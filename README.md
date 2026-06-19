# VAbk Studio

> ⚠️ **Experimental — vibecoded for personal use.** A personal hobby project, built fast and loose
> for my own use and shared as-is: no guarantees, no support, no stability promises. Expect rough
> edges, and use at your own risk.

An all-in-one desktop app for turning books into **visual audiobooks** — a video that shows
word-synced karaoke captions over a canvas while the narration plays.

It drives the two best-in-class tools for the job and removes the fiddly parts:

1. **Abogen** (Kokoro TTS) — EPUB/PDF/text → `.m4b` audio **+** synchronized `.ass` captions.
2. **ffmpeg** — burns the captions onto a canvas and muxes the audio → `.mp4`.

VAbk Studio is the GUI/orchestrator. It does **not** embed Abogen or ffmpeg; it controls them as
external processes — so the app itself stays tiny and the heavy pieces are fetched, on demand,
straight from their upstream sources.

**Runs on Windows and macOS (Apple Silicon).** For real-time-ish speed you want a GPU:
**NVIDIA (CUDA + NVENC)** on Windows, or **Apple Silicon (Metal/MPS + VideoToolbox)** on a Mac.
It also runs CPU-only, just slower.

---

## Quick start (run from source)

You need one thing on your PATH: [**uv**](https://docs.astral.sh/uv/) (recommended) **or** Python
3.12. Everything else is fetched automatically on first run.

```bash
git clone https://github.com/stanyanman/VAbk-Studio.git
cd VAbk-Studio
```

**Windows** — double-click **`Start VAbk Studio.bat`** (or run it in a terminal).

**macOS** — make the launcher executable once, then double-click it in Finder:

```bash
chmod +x "Start VAbk Studio.command"
./"Start VAbk Studio.command"
```

The launcher creates an isolated `.venv`, installs the small GUI dependencies, and starts the app.
First launch also asks **where to keep your files** and creates an `Input/`, `Output/`, and
`Visual Audiobooks/` folder there (default: inside this folder).

<details>
<summary>Prefer the manual steps?</summary>

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/Scripts/python.exe -r requirements.txt   # macOS/Linux: .venv/bin/python
.venv/Scripts/python.exe run.py                                        # macOS/Linux: .venv/bin/python run.py
```
</details>

### What gets downloaded, and when
- **ffmpeg** — if it isn't already on your PATH, the app downloads a pinned, **checksum-verified**
  static build the first time you render a video (Windows ~106 MB / macOS ~45 MB), into the app's
  data folder. Nothing is committed to this repo. You can also point it at your own ffmpeg on the
  **Settings** tab.
- **Abogen** (only needed for the Full Pipeline / Abogen tabs) — on first use click **Set up
  Abogen**. It installs Abogen from GitHub into an isolated `uv` environment. This is a **multi-GB,
  one-time** download (PyTorch + Kokoro voice models), with the right GPU build chosen automatically
  (CUDA on NVIDIA, MPS on Apple Silicon, else CPU).

> **macOS note:** Kokoro's phonemizer may need `espeak-ng`. If narration generation complains, run
> `brew install espeak-ng`. ffmpeg/uv can also be installed via Homebrew (`brew install ffmpeg uv`)
> if you'd rather not let the app fetch ffmpeg.

---

## The four tabs

| Tab | What it does |
|---|---|
| **Full Pipeline** | EPUB/PDF/TXT → audiobook → video, in one click. Runs Abogen then ffmpeg. |
| **Abogen** | Audio-only: EPUB/PDF/TXT → `.m4b` + `.ass` (no video). |
| **FFMPEG** | You already have `.m4b` + `.ass` — batch-render videos with toggleable settings. |
| **Settings** | Tool paths, the ffmpeg auto-download button, and your default folders. |

The GPU checkbox shows the **active device** (e.g. *"MPS GPU available and enabled."*) once Abogen is
set up, so you can confirm acceleration is really on — not silently CPU.

---

## Default video preset (with toggles)

| Setting | Default | Notes |
|---|---|---|
| Resolution / FPS | 1920×1080 / 24 | toggle 480p–4K, any fps |
| Background | solid black | or solid color / image |
| Video codec | `hevc_nvenc` (Win) · `hevc_videotoolbox` (Mac) | + H.264/AV1 NVENC, VideoToolbox, x265/x264, AV1 CPU — auto-detected |
| Quality | CQ `35` (NVENC) · `Q 60` (VideoToolbox) · CRF (CPU) | rate control matches the encoder |
| Audio | Opus 48 kbps mono | or AAC / copy original |
| Subtitles | burned-in | or soft track (mkv) |
| Chapters | carried into the video | from the `.m4b` chapter markers |

**Output filename** encodes the settings, e.g. `Title_1080p_24fps_hevc_cq35_opus48k_p5.mp4`.
The notorious Windows subtitle-path escaping is sidestepped entirely: the `.ass` is copied to a temp
`sub.ass` and ffmpeg runs in that folder, so any book title (apostrophes, commas, colons) just works.

---

## How it works (architecture)

```
VAbk Studio (PyQt6 GUI)
├─ provisioning.py    uv → install Abogen from GitHub (isolated env); resolve/auto-download ffmpeg
├─ abogen_client.py   runs abogen_driver.py with the Abogen env's Python; parses progress
│   └─ abogen_driver.py  (runs INSIDE the Abogen env) extract → build pending job →
│                         ConversionService.enqueue → run_conversion_job → .m4b + .ass
└─ video_builder.py   ffmpeg → burn .ass onto canvas + mux audio → .mp4
```

Abogen has no real argument CLI, and its web UI is a stateful wizard. Instead of scraping that, the
app calls Abogen's own pipeline in-process via a small driver script — exactly what Abogen's "finish"
button does — robust and version-resilient. The driver reports progress and file paths over stdout.

| File | Role |
|---|---|
| `app/main.py` | window + tabs + first-run workspace dialog |
| `app/paths.py` | config dir / app root / ffmpeg cache (cross-platform) |
| `app/settings.py` | preferences + workspace folders |
| `app/provisioning.py` | uv / Abogen install · GPU detection · ffmpeg auto-download |
| `app/video_builder.py` | ffmpeg engine (also a CLI: `python -m app.video_builder --help`) |
| `app/abogen_client.py` · `app/abogen_driver.py` | Abogen integration |
| `app/ui/*` | tabs, dialogs, and background worker threads |

---

## Build a standalone Windows .exe (optional)

Most people just run from source. If you want a double-click `.exe`:

```powershell
.\build.ps1     # creates .venv, installs deps + PyInstaller, builds dist\VAbkStudio.exe
```

---

## Requirements
- Windows 10/11 or macOS (Apple Silicon recommended).
- [`uv`](https://docs.astral.sh/uv/) **or** Python 3.12 on PATH.
- A GPU for acceleration: NVIDIA (CUDA/NVENC) or Apple Silicon (MPS/VideoToolbox). CPU works, slower.
- ffmpeg is auto-downloaded if not on PATH.

Everything the app generates — settings, the Abogen environment, ffmpeg, and uv's package cache —
lives in a **`data/` folder inside the app folder**, so a clone is fully self-contained (delete the
folder = clean uninstall). Point it elsewhere with the `VABK_DATA_DIR` environment variable.

---

## Credits & acknowledgments

VAbk Studio is a thin GUI **orchestrator**. The heavy lifting is done by outstanding open-source
projects, used here as external tools under their own licenses — this app installs and drives them,
it does not bundle or modify them:

- **[Abogen](https://github.com/denizsafak/abogen)** by **Deniz Şafak** — turns EPUB/PDF/text into
  narrated audio with word-synced captions (via [Kokoro](https://github.com/hexgrad/kokoro) TTS).
  Licensed under the **MIT License**, © 2025 Deniz Şafak.
- **[ffmpeg](https://ffmpeg.org/)** — renders the captions onto the canvas and muxes the audio.
  The app downloads official static builds at runtime (it does not redistribute them).

VAbk Studio is an independent project, not affiliated with or endorsed by the Abogen or Kokoro
authors. All trademarks and copyrights belong to their respective owners.
