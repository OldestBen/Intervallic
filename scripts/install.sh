#!/usr/bin/env bash
# Intervallic installer
# Supports: Debian/Ubuntu (apt), macOS (brew), any system with pip
# Run as root on Linux servers; as a normal user on macOS/desktop.
set -euo pipefail

VENV_DIR="${1:-.venv}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

# ── Colours (no-op when not a tty) ────────────────────────────────────────────
if [ -t 1 ]; then
    GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; RESET='\033[0m'
else
    GREEN=''; YELLOW=''; RED=''; RESET=''
fi

info()    { echo -e "${GREEN}[✓]${RESET} $*"; }
warn()    { echo -e "${YELLOW}[!]${RESET} $*"; }
die()     { echo -e "${RED}[✗]${RESET} $*" >&2; exit 1; }
section() { echo -e "\n${YELLOW}──${RESET} $*"; }

# ── Platform detection ────────────────────────────────────────────────────────
OS="$(uname -s)"
case "$OS" in
    Linux)
        if command -v apt-get &>/dev/null; then DISTRO="debian"
        elif command -v dnf &>/dev/null;    then DISTRO="fedora"
        elif command -v yum &>/dev/null;    then DISTRO="rhel"
        elif command -v pacman &>/dev/null; then DISTRO="arch"
        else                                     DISTRO="unknown"
        fi
        ;;
    Darwin) DISTRO="macos" ;;
    *)      DISTRO="unknown" ;;
esac

# ── Privilege helper ──────────────────────────────────────────────────────────
# On Linux servers running as root, sudo doesn't exist — just run directly.
run_privileged() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    elif command -v sudo &>/dev/null; then
        sudo "$@"
    else
        die "Need root to install system packages. Run as root or install sudo."
    fi
}

# ── Step 1: Ensure python3 exists ────────────────────────────────────────────
section "Checking Python"

if ! command -v python3 &>/dev/null; then
    warn "python3 not found — installing …"
    case "$DISTRO" in
        debian) run_privileged apt-get install -y python3 ;;
        fedora) run_privileged dnf install -y python3 ;;
        rhel)   run_privileged yum install -y python3 ;;
        arch)   run_privileged pacman -S --noconfirm python ;;
        macos)
            command -v brew &>/dev/null || die "Homebrew not found. Install from https://brew.sh then re-run."
            brew install python3
            ;;
        *) die "Cannot install python3 automatically. Please install it manually." ;;
    esac
fi

PYTHON=$(command -v python3)
PY_VER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
info "Python $PY_VER at $PYTHON"

# ── Step 2: Ensure venv support is available ─────────────────────────────────
section "Checking venv support"

if ! "$PYTHON" -m venv --help &>/dev/null 2>&1; then
    warn "venv module missing — installing …"
    case "$DISTRO" in
        debian) run_privileged apt-get install -y "python${PY_VER}-venv" ;;
        fedora) run_privileged dnf install -y "python${PY_VER}" ;;
        rhel)   run_privileged yum install -y "python${PY_VER}" ;;
        arch)   info "venv is built into Arch python — nothing to install." ;;
        macos)  info "venv is built into Homebrew python — nothing to install." ;;
        *) die "venv not available. Install python${PY_VER}-venv manually." ;;
    esac
fi

# On Debian, venv may be importable but ensurepip broken — test with a dry run
if ! "$PYTHON" -m venv --without-pip /tmp/_venv_probe 2>/dev/null; then
    :  # probe itself failed, will surface properly below
fi
rm -rf /tmp/_venv_probe

# Test that a venv created by this Python actually works (ensurepip check)
PROBE=$(mktemp -d)
trap 'rm -rf "$PROBE"' EXIT
if ! "$PYTHON" -m venv "$PROBE" 2>/dev/null || [ ! -f "$PROBE/bin/python" ]; then
    warn "Venv probe failed — installing python${PY_VER}-venv …"
    case "$DISTRO" in
        debian) run_privileged apt-get install -y "python${PY_VER}-venv" ;;
        *) die "Cannot create a working venv. Install python${PY_VER}-venv manually." ;;
    esac
fi
trap - EXIT
rm -rf "$PROBE"

info "venv support OK"

# ── Step 3: Remove any broken existing venv ──────────────────────────────────
section "Preparing virtual environment"

VENV_PATH="$ROOT_DIR/$VENV_DIR"

if [ -d "$VENV_PATH" ]; then
    # A venv is broken if its python binary is missing or can't import pip
    if ! "$VENV_PATH/bin/python" -c "import pip" &>/dev/null 2>&1; then
        warn "Existing venv at $VENV_DIR is broken — removing …"
        rm -rf "$VENV_PATH"
    else
        info "Existing venv looks healthy."
    fi
fi

if [ ! -d "$VENV_PATH" ]; then
    info "Creating venv at $VENV_DIR …"
    "$PYTHON" -m venv "$VENV_PATH"
fi

# ── Step 4: Install dependencies ─────────────────────────────────────────────
section "Installing dependencies"

"$VENV_PATH/bin/pip" install --quiet --upgrade pip
"$VENV_PATH/bin/pip" install --quiet -r "$ROOT_DIR/requirements.txt"
"$VENV_PATH/bin/pip" install --quiet -e "$ROOT_DIR"

info "All dependencies installed."

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}Installation complete.${RESET}"
echo ""
echo "  Activate:              source $VENV_DIR/bin/activate"
echo "  Run the setup wizard:  $VENV_DIR/bin/intervallic setup"
echo "  Sync playlists:        $VENV_DIR/bin/intervallic sync"
echo ""
echo "  Or use make targets:   make setup  |  make sync  |  make dry-run"
