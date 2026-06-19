"""Launcher for VAbk Studio (dev: `python run.py`; also the PyInstaller entry)."""
import sys

from app.main import main

if __name__ == "__main__":
    sys.exit(main())
