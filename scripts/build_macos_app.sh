#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_PYTHON="$PROJECT_DIR/.venv/bin/python"
PYINSTALLER="$PROJECT_DIR/.venv/bin/pyinstaller"
SPEC_FILE="$PROJECT_DIR/AstrBot Desktop Assistant.spec"
APP_BUNDLE="$PROJECT_DIR/dist/AstrBot Desktop Assistant.app"

echo "[build] project: $PROJECT_DIR"

if [[ ! -x "$VENV_PYTHON" ]]; then
    echo "[build] missing virtualenv python: $VENV_PYTHON" >&2
    echo "[build] run install.sh or create .venv first" >&2
    exit 1
fi

if [[ ! -x "$PYINSTALLER" ]]; then
    echo "[build] installing pyinstaller into .venv"
    "$VENV_PYTHON" -m pip install pyinstaller
fi

cd "$PROJECT_DIR"
"$PYINSTALLER" --noconfirm "$SPEC_FILE"

if [[ ! -d "$APP_BUNDLE" ]]; then
    echo "[build] app bundle not found after build: $APP_BUNDLE" >&2
    exit 1
fi

echo "[build] done: $APP_BUNDLE"
