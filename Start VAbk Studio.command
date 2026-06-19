#!/usr/bin/env bash
# ============================================================
#  VAbk Studio - one-click launcher (macOS: double-click in Finder; also Linux).
#
#  Self-contained and location-independent: it always uses the .venv INSIDE this
#  script's own folder, no matter where the folder lives. If that .venv is
#  missing, incomplete, or was built on another machine (its base interpreter is
#  gone), it is rebuilt in place. No dependency on anything "above" this folder.
# ============================================================
set -u

# A Finder double-click runs under launchd's minimal PATH (/usr/bin:/bin:...), which
# does NOT include Homebrew or the uv / standalone-installer locations. Prepend the
# usual spots so a double-click can find uv/python3 that work in Terminal. Existing
# entries are kept and still take precedence for anything already resolvable.
export PATH="/opt/homebrew/bin:/usr/local/bin:${HOME:-}/.local/bin:${HOME:-}/.cargo/bin:$PATH"

# Resolve this script's own directory (absolute) and work from there.
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$APP_DIR" || { echo "Cannot enter script directory: $APP_DIR"; exit 1; }

VENV_DIR="$APP_DIR/.venv"
VENV_PY="$VENV_DIR/bin/python"

# A venv is valid for THIS machine only if its python runs AND the app's deps
# import. Checking just the interpreter would let a half-finished "pip install"
# (e.g. interrupted by a network drop) pass and then fail silently at launch;
# importing the actual deps (the app is pure PyQt6 + requests) catches that. A
# moved/removed base interpreter also fails this check, so the venv is rebuilt.
venv_ok() {
    [ -x "$VENV_PY" ] && "$VENV_PY" -c 'import PyQt6, requests' >/dev/null 2>&1
}

build_venv() {
    echo "=== Setting up VAbk Studio (one-time) ==="
    if command -v uv >/dev/null 2>&1; then
        uv venv --python 3.12 "$VENV_DIR" || return 1
        uv pip install --python "$VENV_PY" -r "$APP_DIR/requirements.txt" || return 1
    elif command -v python3 >/dev/null 2>&1; then
        python3 -m venv "$VENV_DIR" || return 1
        "$VENV_PY" -m pip install --upgrade pip || return 1
        "$VENV_PY" -m pip install -r "$APP_DIR/requirements.txt" || return 1
    else
        echo "No 'uv' or 'python3' found on PATH."
        echo "Install Python 3.12 (https://www.python.org) or uv (https://docs.astral.sh/uv/) and retry."
        return 1
    fi
}

if ! venv_ok; then
    if [ -e "$VENV_DIR" ]; then
        echo "=== The existing .venv is not valid here - rebuilding it ==="
        rm -rf "$VENV_DIR"
    fi
    if ! build_venv || ! venv_ok; then
        echo
        echo "Setup failed. See the messages above."
        read -r -p "Press Enter to close. " _ || true
        exit 1
    fi
fi

# macOS note: Kokoro's phonemizer (used later by the separate Abogen runtime, not
# this .venv) may need espeak-ng:  brew install espeak-ng
#
# Launch detached so closing the Terminal window does not kill the app. Output goes
# to a log (gitignored) instead of /dev/null, so a startup crash leaves something to
# read; fall back to the app folder if data/ cannot be created.
LOG_DIR="$APP_DIR/data"
mkdir -p "$LOG_DIR" 2>/dev/null || LOG_DIR="$APP_DIR"
nohup "$VENV_PY" run.py "$@" >"$LOG_DIR/launch.log" 2>&1 &
disown 2>/dev/null || true
