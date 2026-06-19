# VAbk Studio

> ⚠️ **Experimental — vibecoded for personal use.** A personal hobby project, built fast and loose
> for my own use and shared as-is: no guarantees, no support, no stability promises. Expect rough
> edges, and use at your own risk.

A **Windows** desktop app for turning books into **visual audiobooks** — a video that shows
word-synced karaoke captions over a canvas while the narration plays.

It drives the two best-in-class tools for the job and removes the fiddly parts:

1. **Abogen** (Kokoro TTS) — EPUB/PDF/text → `.m4b` audio **+** synchronized `.ass` captions.
2. **ffmpeg** — burns the captions onto a canvas and muxes the audio → `.mp4`.

VAbk Studio is the GUI/orchestrator. It doesn't embed Abogen or ffmpeg; it downloads and drives them
on demand. Everything it fetches lands in a **`data/` folder right next to the app**, so the whole
thing is **portable and self-contained** — move the folder, delete it to uninstall, nothing is
scattered across your user profile.

An **NVIDIA GPU (CUDA + NVENC)** makes it fast; without one it falls back to CPU (much slower).

---

## Get it running

### Option 1 — Download the app (no Python needed)
Grab **`VAbkStudio.exe`** from the [**Releases**](https://github.com/stanyanman/VAbk-Studio/releases)
page, drop it in **its own folder**, and double-click it. That's it — there's no installer and no
Python to set up.

On first launch it asks where to keep your files and makes an `Input/`, `Output/`, and
`Visual Audiobooks/` folder, then downloads what it needs into `data/` next to the exe (see below).

### Option 2 — Run from source
```powershell
git clone https://github.com/stanyanman/VAbk-Studio.git
cd VAbk-Studio
```
Then double-click **`Start VAbk Studio.bat`**. It creates an isolated `.venv`, installs the small GUI
dependencies, and launches the app. (Needs [`uv`](https://docs.astral.sh/uv/) **or** Python 3.12 — the
launcher uses whichever it finds.)

---

## What it downloads, and where
Everything goes into a **`data/` folder beside the app** — nothing touches your user profile:

| In `data/` | What | When |
|---|---|---|
| `ffmpeg/` | pinned, checksum-verified static ffmpeg (~106 MB) | first time you render a video |
| `uv/` | the `uv` package manager (if not already installed) | first time you set up Abogen |
| `abogen-runtime/` | Abogen + PyTorch + Kokoro voice models (several GB) | when you click **Download Dependencies** |
| `hf-cache/` | HuggingFace model cache (Kokoro) | during Abogen generation |
| `uv-cache/`, `config.json` | uv's package cache, your settings | as needed |

Because it's all in the folder, you can zip it up and move it. To point the data elsewhere, set the
`VABK_DATA_DIR` environment variable. To uninstall, just delete the folder.

---

## The four tabs

| Tab | What it does |
|---|---|
| **Full Pipeline** | EPUB/PDF/TXT → audiobook → video, in one click. Runs Abogen then ffmpeg. |
| **Abogen** | Audio-only: EPUB/PDF/TXT → `.m4b` + `.ass` (no video). |
| **FFMPEG** | You already have `.m4b` + `.ass` — batch-render videos with toggleable settings. |
| **Settings** | Tool paths, the ffmpeg auto-download button, and your default folders. |

The Full Pipeline / Abogen tabs show **Abogen** and **FFmpeg** status indicators and a single
**Download Dependencies** button that fetches whatever's missing. The GPU checkbox shows the active
device (e.g. *"CUDA GPU available and enabled."*) so you can confirm acceleration is really on.

---

## Default video preset (with toggles)

| Setting | Default | Notes |
|---|---|---|
| Resolution / FPS | 1920×1080 / 24 | toggle 480p–4K, any fps |
| Background | solid black | or solid color / image |
| Video codec | `hevc_nvenc` | + H.264/AV1 NVENC, x265/x264, AV1 CPU — auto-detected |
| Quality | CQ `35` (NVENC) · CRF (CPU) | rate control matches the encoder |
| Audio | Opus 48 kbps mono | or AAC / copy original |
| Subtitles | burned-in | or soft track (mkv) |
| Chapters | carried into the video | from the `.m4b` chapter markers |

**Output filename** encodes the settings, e.g. `Title_1080p_24fps_hevc_cq35_opus48k_p5.mp4`.
The notorious Windows subtitle-path escaping is sidestepped entirely: the `.ass` is copied to a temp
`sub.ass` and ffmpeg runs in that folder, so any book title (apostrophes, commas, colons) just works.

---

## How it works (architecture)

```
VAbkStudio.exe (PyQt6 GUI)
├─ provisioning.py    download uv + Abogen (isolated env) + ffmpeg, all into data/
├─ abogen_client.py   runs abogen_driver.py with the Abogen env's Python; parses progress
│   └─ abogen_driver.py  (runs INSIDE the Abogen env) extract → build pending job →
│                         ConversionService.enqueue → run_conversion_job → .m4b + .ass
└─ video_builder.py   ffmpeg → burn .ass onto canvas + mux audio → .mp4
```

Abogen has no real argument CLI, and its web UI is a stateful wizard. Instead of scraping that, the
app calls Abogen's own pipeline in-process via a small driver script — exactly what Abogen's "finish"
button does — robust and version-resilient. The driver reports progress and file paths over stdout.

---

## Build the .exe yourself

```powershell
.\build.ps1     # creates .venv, installs deps + PyInstaller, builds dist\VAbkStudio.exe
```

Pushing a version tag (`git tag v0.1.0 && git push origin v0.1.0`) triggers GitHub Actions to build
the exe and attach it to a Release automatically.

---

## Requirements
- Windows 10/11.
- An NVIDIA GPU (CUDA/NVENC) for acceleration — CPU works but is much slower.
- For the **exe**: nothing else. For **run-from-source**: `uv` or Python 3.12 on PATH.
- ffmpeg and uv are auto-downloaded into `data/` if not already present.

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
