#!/usr/bin/env bash
# install.sh
set -euo pipefail

APP_NAME="Sirgon DiskQual"
APP_ROOT="/opt/sirgon-diskqual"
VENV="$APP_ROOT/venv"
DATA_ROOT="/opt/diskqual"
MANIFEST="$APP_ROOT/install-manifest.env"
MIN_PYTHON="3.10"
PACKAGE_MANAGER=""
INSTALLED_BY_SIRGON=""

info() { printf '[INFO] %s\n' "$*"; }
ok() { printf '[ OK ] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*" >&2; }
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
    fail "Run this installer as root, for example: sudo ./install.sh <wheel>"
fi

if [ "$#" -gt 1 ]; then
    fail "Usage: sudo ./install.sh [sirgon_diskqual-<version>-py3-none-any.whl]"
fi

WHEEL="${1:-}"
if [ -z "$WHEEL" ]; then
    WHEEL=$(find "$(pwd)/dist" -maxdepth 1 -type f -name 'sirgon_diskqual-*.whl' -print 2>/dev/null | sort -V | tail -1 || true)
fi

[ -n "$WHEEL" ] || fail "No Sirgon DiskQual wheel supplied and none found under ./dist/."
WHEEL=$(readlink -f "$WHEEL")
[ -f "$WHEEL" ] || fail "Wheel not found: $WHEEL"

if [ "$(uname -s)" != "Linux" ]; then
    fail "$APP_NAME currently supports Linux qualification stations."
fi

package_is_installed() {
    local pkg="$1"
    case "$PACKAGE_MANAGER" in
        apt) dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q 'install ok installed' ;;
        dnf|yum|zypper) rpm -q "$pkg" >/dev/null 2>&1 ;;
        pacman) pacman -Q "$pkg" >/dev/null 2>&1 ;;
        *) return 1 ;;
    esac
}

record_missing_packages() {
    local pkg
    for pkg in "$@"; do
        if ! package_is_installed "$pkg"; then
            INSTALLED_BY_SIRGON="${INSTALLED_BY_SIRGON:+$INSTALLED_BY_SIRGON }$pkg"
        fi
    done
}

install_system_packages() {
    info "Detecting Linux package manager..."
    if command -v apt-get >/dev/null 2>&1; then
        PACKAGE_MANAGER="apt"
        local packages=(python3 python3-venv smartmontools gdisk util-linux e2fsprogs)
        record_missing_packages "${packages[@]}"
        info "Installing requirements with apt..."
        apt-get update
        DEBIAN_FRONTEND=noninteractive apt-get install -y "${packages[@]}"
    elif command -v dnf >/dev/null 2>&1; then
        PACKAGE_MANAGER="dnf"
        local packages=(python3 smartmontools gdisk util-linux e2fsprogs)
        record_missing_packages "${packages[@]}"
        info "Installing requirements with dnf..."
        dnf install -y "${packages[@]}"
    elif command -v yum >/dev/null 2>&1; then
        PACKAGE_MANAGER="yum"
        local packages=(python3 smartmontools gdisk util-linux e2fsprogs)
        record_missing_packages "${packages[@]}"
        info "Installing requirements with yum..."
        yum install -y "${packages[@]}"
    elif command -v zypper >/dev/null 2>&1; then
        PACKAGE_MANAGER="zypper"
        local packages=(python3 python3-pip smartmontools gdisk util-linux e2fsprogs)
        record_missing_packages "${packages[@]}"
        info "Installing requirements with zypper..."
        zypper --non-interactive install "${packages[@]}"
    elif command -v pacman >/dev/null 2>&1; then
        PACKAGE_MANAGER="pacman"
        local packages=(python smartmontools gptfdisk util-linux e2fsprogs)
        record_missing_packages "${packages[@]}"
        info "Installing requirements with pacman..."
        pacman -Sy --needed --noconfirm "${packages[@]}"
    else
        fail "Unsupported package manager. Install Python >= $MIN_PYTHON, smartmontools, gdisk, util-linux, and e2fsprogs manually, then rerun this installer."
    fi
}

check_python() {
    command -v python3 >/dev/null 2>&1 || fail "python3 was not found after package installation."
    python3 - <<'PY' || exit 1
import sys
minimum = (3, 10)
if sys.version_info < minimum:
    raise SystemExit(
        f"[FAIL] Sirgon DiskQual requires Python {minimum[0]}.{minimum[1]} or newer; "
        f"found {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}."
    )
print(f"[ OK ] Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
PY
}

verify_command() {
    command -v "$1" >/dev/null 2>&1 || fail "Required command '$1' is missing. Expected package: $2"
    ok "$1 available ($(command -v "$1"))"
}

install_system_packages
check_python

info "Verifying Linux qualification utilities..."
verify_command smartctl smartmontools
verify_command lsblk util-linux
verify_command blockdev util-linux
verify_command wipefs util-linux
verify_command badblocks e2fsprogs
verify_command sgdisk gdisk/gptfdisk
verify_command dd coreutils
verify_command systemctl systemd

if [ ! -d /run/systemd/system ]; then
    fail "systemd is not running. Persistent qualification jobs currently require a systemd-based Linux system."
fi
ok "systemd is running"

info "Creating application and persistent data directories..."
mkdir -p "$APP_ROOT"
mkdir -p "$DATA_ROOT"/{reports,logs,inventory,labels,client-reports}
chmod 755 "$APP_ROOT" "$DATA_ROOT"

if [ ! -x "$VENV/bin/python" ]; then
    info "Creating managed Python environment at $VENV..."
    python3 -m venv "$VENV" || fail "Could not create Python virtual environment. Install the distribution's Python venv package and retry."
else
    ok "Existing managed Python environment found"
fi

info "Installing $APP_NAME from $(basename "$WHEEL")..."
"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install --upgrade "$WHEEL"

info "Installing command launchers..."
ln -sfn "$VENV/bin/diskqual" /usr/local/bin/diskqual
ln -sfn "$VENV/bin/sirgon-diskqual" /usr/local/bin/sirgon-diskqual
ln -sfn "$VENV/bin/sirgon-diskqual-ui" /usr/local/bin/sirgon-diskqual-ui

cat >/etc/profile.d/sirgon-diskqual.sh <<'EOF'
# Sirgon DiskQual persistent data directory
export DISKQUAL_HOME=/opt/diskqual
EOF
chmod 644 /etc/profile.d/sirgon-diskqual.sh

info "Running post-install verification..."
export DISKQUAL_HOME="$DATA_ROOT"

VERSION_OUTPUT=$(/usr/local/bin/diskqual --version) || fail "Installed diskqual command failed."
ok "$VERSION_OUTPUT"

"$VENV/bin/python" - <<'PY' || exit 1
import diskqual
import diskqual.cli
import diskqual.engine
import diskqual.labels
import diskqual.projects
import diskqual.progress
import diskqual.reporting
import diskqual.tui
print('[ OK ] Sirgon DiskQual Python modules import successfully')
PY

for command in diskqual sirgon-diskqual sirgon-diskqual-ui; do
    [ -x "/usr/local/bin/$command" ] || fail "Launcher /usr/local/bin/$command is not executable."
    ok "$command launcher installed"
done

[ -w "$DATA_ROOT" ] || fail "Persistent data directory is not writable by root: $DATA_ROOT"
ok "Persistent data directory ready: $DATA_ROOT"

# Keep the installation manifest inside APP_ROOT so a normal uninstall removes it.
# Values are generated by this script and contain only package names / simple tokens.
cat >"$MANIFEST" <<EOF
PACKAGE_MANAGER='$PACKAGE_MANAGER'
INSTALLED_BY_SIRGON='$INSTALLED_BY_SIRGON'
INSTALLED_VERSION='${VERSION_OUTPUT#Sirgon DiskQual }'
INSTALLED_UTC='$(date -u +%Y-%m-%dT%H:%M:%SZ)'
EOF
chmod 600 "$MANIFEST"
ok "Installation manifest recorded"

echo
printf '%s installation completed successfully.\n' "$APP_NAME"
printf 'Application:  %s\n' "$APP_ROOT"
printf 'Data:         %s\n' "$DATA_ROOT"
printf 'CLI:          %s\n' "diskqual"
printf 'Interface:    %s\n' "sirgon-diskqual-ui"
printf 'Version:      %s\n' "$VERSION_OUTPUT"
echo
echo "Recommended first checks:"
echo "  diskqual --version"
echo "  diskqual inventory"
echo "  sirgon-diskqual-ui"
