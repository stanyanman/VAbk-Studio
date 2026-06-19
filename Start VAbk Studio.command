#!/usr/bin/env bash
# One-click launcher for VAbk Studio on macOS (double-click in Finder) and Linux.
# First run creates an isolated .venv and installs dependencies; later runs are instant.
set -e
cd "$(dirname "$0")"

VENV_PY=".venv/bin/python"

if [ ! -x "$VENV_PY" ]; then
    echo "=== First run: setting up VAbk Studio ==="
    if command -v uv >/dev/null 2>&1; then
        uv venv --python 3.12 .venv
        uv pip install --python "$VENV_PY" -r requirements.txt
    elif command -v python3 >/dev/null 2>&1; then
        python3 -m venv .venv
        "$VENV_PY" -m pip install --upgrade pip
        "$VENV_PY" -m pip install -r requirements.txt
    else
        echo "No 'uv' or 'python3' found on PATH."
        echo "Install Python 3.12 (https://www.python.org) or uv (https://docs.astral.sh/uv/) and retry."
        read -r -p "Press Enter to close."
        exit 1
    fi
fi

# Launch detached so closing the Terminal window doesn't kill the app.
nohup "$VENV_PY" run.py "$@" >/dev/null 2>&1 &
disown 2>/dev/null || true
