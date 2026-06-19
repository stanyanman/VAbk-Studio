"""VAbk Studio — all-in-one GUI for turning EPUBs into visual audiobooks.

Stage 1 (Abogen): EPUB/PDF/text -> .m4b audio + word-synced .ass karaoke subtitles.
Stage 2 (ffmpeg): burn the .ass onto a canvas and mux with the audio -> .mp4 video.

This package is the GUI/orchestrator. It does not embed Abogen or ffmpeg; it drives
them as external processes (see provisioning.py / abogen_client.py / video_builder.py).
"""

__version__ = "0.1.0"
APP_NAME = "VAbk Studio"
