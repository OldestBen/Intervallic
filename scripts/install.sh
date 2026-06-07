#!/usr/bin/env bash
# Sets up a Python virtual environment and installs dependencies.
# Handles Debian/Ubuntu externally-managed-environment and missing ensurepip.
# Safe to run multiple times.
set -euo pipefail

VENV_DIR="${1:-.venv}"

# ── Ensure python3 is present ─────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo "[error] python3 not found."
    echo "        Run: apt install python3"
    exit 1
fi

PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "Python $PY_VERSION detected."

# ── Ensure python3-venv / ensurepip is present ───────────────────────────────
if ! python3 -m ensurepip --version &>/dev/null 2>&1; then
    echo "ensurepip not available — installing python${PY_VERSION}-venv …"
    if command -v apt-get &>/dev/null; then
        apt-get install -y "python${PY_VERSION}-venv"
    elif command -v apt &>/dev/null; then
        apt install -y "python${PY_VERSION}-venv"
    else
        echo "[error] apt not found. Install python${PY_VERSION}-venv manually."
        exit 1
    fi
fi

# ── Create venv ───────────────────────────────────────────────────────────────
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment in $VENV_DIR …"
    python3 -m venv "$VENV_DIR"
else
    echo "Virtual environment already exists at $VENV_DIR."
fi

# ── Bootstrap pip if missing (e.g. --without-pip venvs) ──────────────────────
if [ ! -f "$VENV_DIR/bin/pip" ]; then
    echo "pip not found in venv — bootstrapping …"
    python3 -m ensurepip --upgrade
    "$VENV_DIR/bin/python" -m ensurepip --upgrade
fi

# ── Install dependencies ──────────────────────────────────────────────────────
echo "Upgrading pip …"
"$VENV_DIR/bin/pip" install --quiet --upgrade pip

echo "Installing dependencies …"
"$VENV_DIR/bin/pip" install --quiet -r requirements.txt
"$VENV_DIR/bin/pip" install --quiet -e .

echo ""
echo "✓ Installation complete."
echo ""
echo "  Activate the environment:   source $VENV_DIR/bin/activate"
echo "  Run the setup wizard:       $VENV_DIR/bin/intervallic setup"
echo "  Or use make targets:        make setup | make sync | make dry-run"
