#!/usr/bin/env bash
# Bootstrap a working Python venv and install Intervallic.
# Handles Debian/Ubuntu externally-managed-environment restrictions.
# Run as root on a fresh system, or as a user with sudo.
set -euo pipefail

VENV_DIR="${1:-.venv}"

# ── Helpers ───────────────────────────────────────────────────────────────────

apt_install() {
    if command -v apt-get &>/dev/null; then
        apt-get install -y "$@"
    else
        echo "[error] apt-get not found. Install manually: $*" >&2
        exit 1
    fi
}

# ── 1. Require python3 ────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo "python3 not found — installing …"
    apt_install python3
fi

PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "Python $PY_VER found."

# ── 2. Ensure python3.X-venv is installed ────────────────────────────────────
if ! dpkg -s "python${PY_VER}-venv" &>/dev/null 2>&1; then
    echo "Installing python${PY_VER}-venv …"
    apt_install "python${PY_VER}-venv"
fi

# ── 3. Remove any broken venv (created before venv package was installed) ─────
if [ -d "$VENV_DIR" ] && ! "$VENV_DIR/bin/python" -c "import ensurepip" &>/dev/null 2>&1; then
    echo "Removing broken virtual environment …"
    rm -rf "$VENV_DIR"
fi

# ── 4. Create venv ────────────────────────────────────────────────────────────
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment in $VENV_DIR …"
    python3 -m venv "$VENV_DIR"
fi

# ── 5. Install dependencies ───────────────────────────────────────────────────
echo "Upgrading pip …"
"$VENV_DIR/bin/pip" install --quiet --upgrade pip

echo "Installing dependencies …"
"$VENV_DIR/bin/pip" install --quiet -r requirements.txt
"$VENV_DIR/bin/pip" install --quiet -e .

echo ""
echo "Done."
echo ""
echo "  Activate:       source $VENV_DIR/bin/activate"
echo "  Setup wizard:   $VENV_DIR/bin/intervallic setup"
echo "  Or use make:    make setup | make sync | make dry-run"
