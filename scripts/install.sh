#!/usr/bin/env bash
# Sets up a Python virtual environment and installs dependencies.
# Safe to run multiple times.
set -euo pipefail

VENV_DIR="${1:-.venv}"

if ! command -v python3 &>/dev/null; then
    echo "python3 not found. Install it with: apt install python3 python3-venv"
    exit 1
fi

# python3-venv may not be installed even when python3 is
if ! python3 -c "import venv" &>/dev/null; then
    echo "python3-venv not found. Installing…"
    apt-get install -y python3-venv python3-full
fi

if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment in $VENV_DIR …"
    python3 -m venv "$VENV_DIR"
fi

echo "Installing dependencies…"
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install -r requirements.txt

echo ""
echo "Done. Activate with:"
echo "  source $VENV_DIR/bin/activate"
echo ""
echo "Or run directly:"
echo "  $VENV_DIR/bin/intervallic setup"
