#!/usr/bin/env bash
# install.sh
set -euo pipefail

APP_NAME="Sirgon DiskQual"
APP_ROOT="/opt/sirgon-diskqual"
VENV="$APP_ROOT/venv"
DATA_ROOT="/opt/diskqual"
MANIFEST="$APP_ROOT/install-manifest.env"
REPO="FernandoAFOliveira/Sirgon-DiskQual"
MIN_PYTHON="3.10"
PACKAGE_MANAGER=""
INSTALLED_BY_SIRGON=""
PREVIOUS_INSTALLED_BY_SIRGON=""
LOCAL_WHEEL=""
REQUESTED_TAG=""
RELEASE_TAG=""
TEMP_DIR=""

info() { printf '[INFO] %s\n' "$*"; }
ok() { printf '[ OK ] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*" >&2; }
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }

cleanup() {
    if [ -n "$TEMP_DIR" ] && [ -d "$TEMP_DIR" ]; then
        rm -rf "$TEMP_DIR"
    fi
}
trap cleanup EXIT

usage() {
    cat <<'EOF'
Sirgon DiskQual installer

Normal user installation:
  sudo ./install.sh

Install a specific GitHub release:
  sudo ./install.sh --release v0.3.0-beta.3

Developer/local package installation:
  sudo ./install.sh /path/to/sirgon_diskqual-<version>-py3-none-any.whl
EOF
}

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
    fail "Run this installer as root, for example: sudo ./install.sh"
fi

while [ "$#" -gt 0 ]; do
    case "$1" in
        --release)
            [ "$#" -ge 2 ] || fail "--release requires a tag, for example v0.3.0-beta.3"
            REQUESTED_TAG="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        -* )
            fail "Unknown option: $1"
            ;;
        *)
            [ -z "$LOCAL_WHEEL" ] || fail "Only one local wheel may be supplied."
            LOCAL_WHEEL="$1"
            shift
            ;;
    esac
done

if [ -n "$LOCAL_WHEEL" ] && [ -n "$REQUESTED_TAG" ]; then
    fail "Use either a local wheel or --release, not both."
fi

if [ "$(uname -s)" != "Linux" ]; then
    fail "$APP_NAME currently supports Linux qualification stations."
fi

# Preserve the dependency ownership record across upgrades. Without this, an
# upgrade performed after the dependencies are already present would replace
# the manifest with an empty list and a later clean uninstall could no longer
# identify packages originally installed by Sirgon DiskQual.
if [ -f "$MANIFEST" ]; then
    PREVIOUS_INSTALLED_BY_SIRGON=$(sed -n "s/^INSTALLED_BY_SIRGON='\(.*\)'$/\1/p" "$MANIFEST" | head -1)
    INSTALLED_BY_SIRGON="$PREVIOUS_INSTALLED_BY_SIRGON"
    if [ -n "$PREVIOUS_INSTALLED_BY_SIRGON" ]; then
        info "Preserving previously recorded installer-added packages: $PREVIOUS_INSTALLED_BY_SIRGON"
    fi
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
            case " $INSTALLED_BY_SIRGON " in
                *" $pkg "*) ;;
                *) INSTALLED_BY_SIRGON="${INSTALLED_BY_SIRGON:+$INSTALLED_BY_SIRGON }$pkg" ;;
            esac
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

resolve_release() {
    local tag="$1"
    python3 - "$REPO" "$tag" <<'PY'
import json
import sys
import urllib.error
import urllib.request

repo, requested = sys.argv[1], sys.argv[2]
headers = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "Sirgon-DiskQual-Installer",
}

def get_json(url):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)

base = f"https://api.github.com/repos/{repo}"
if requested:
    release = get_json(f"{base}/releases/tags/{requested}")
else:
    try:
        release = get_json(f"{base}/releases/latest")
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        releases = get_json(f"{base}/releases?per_page=20")
        release = next((item for item in releases if not item.get("draft")), None)
        if not release:
            raise SystemExit("[FAIL] No published Sirgon DiskQual release was found.")

assets = release.get("assets", [])
wheel = next(
    (asset for asset in assets if asset.get("name", "").startswith("sirgon_diskqual-") and asset.get("name", "").endswith(".whl")),
    None,
)
if not wheel:
    raise SystemExit(f"[FAIL] Release {release.get('tag_name', '?')} has no Sirgon DiskQual wheel asset.")

print(release["tag_name"])
print(wheel["browser_download_url"])
print(wheel["name"])
PY
}

download_file() {
    local url="$1"
    local destination="$2"
    python3 - "$url" "$destination" <<'PY'
import sys
import urllib.request

url, destination = sys.argv[1], sys.argv[2]
request = urllib.request.Request(url, headers={"User-Agent": "Sirgon-DiskQual-Installer"})
with urllib.request.urlopen(request, timeout=120) as response, open(destination, "wb") as output:
    while True:
        chunk = response.read(1024 * 1024)
        if not chunk:
            break
        output.write(chunk)
PY
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

if [ -n "$LOCAL_WHEEL" ]; then
    WHEEL=$(readlink -f "$LOCAL_WHEEL")
    [ -f "$WHEEL" ] || fail "Wheel not found: $WHEEL"
    info "Using local package: $WHEEL"
else
    info "Finding Sirgon DiskQual release on GitHub..."
    mapfile -t RELEASE_INFO < <(resolve_release "$REQUESTED_TAG")
    [ "${#RELEASE_INFO[@]}" -eq 3 ] || fail "Could not resolve a downloadable Sirgon DiskQual release."
    RELEASE_TAG="${RELEASE_INFO[0]}"
    WHEEL_URL="${RELEASE_INFO[1]}"
    WHEEL_NAME="${RELEASE_INFO[2]}"
    TEMP_DIR=$(mktemp -d)
    WHEEL="$TEMP_DIR/$WHEEL_NAME"
    info "Downloading $APP_NAME $RELEASE_TAG..."
    download_file "$WHEEL_URL" "$WHEEL"
    [ -s "$WHEEL" ] || fail "Downloaded wheel is empty: $WHEEL"
    ok "Downloaded $WHEEL_NAME"
fi

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

if [ -n "$RELEASE_TAG" ]; then
    UNINSTALL_URL="https://raw.githubusercontent.com/$REPO/$RELEASE_TAG/uninstall.sh"
    info "Installing standalone uninstaller..."
    if download_file "$UNINSTALL_URL" /usr/local/sbin/sirgon-diskqual-uninstall; then
        chmod 755 /usr/local/sbin/sirgon-diskqual-uninstall
        ok "Uninstaller installed: /usr/local/sbin/sirgon-diskqual-uninstall"
    else
        warn "Could not download the standalone uninstaller. The application installation will continue."
        rm -f /usr/local/sbin/sirgon-diskqual-uninstall
    fi
elif [ -f ./uninstall.sh ]; then
    cp ./uninstall.sh /usr/local/sbin/sirgon-diskqual-uninstall
    chmod 755 /usr/local/sbin/sirgon-diskqual-uninstall
    ok "Local uninstaller installed"
fi

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

cat >"$MANIFEST" <<EOF
PACKAGE_MANAGER='$PACKAGE_MANAGER'
INSTALLED_BY_SIRGON='$INSTALLED_BY_SIRGON'
INSTALLED_VERSION='${VERSION_OUTPUT#Sirgon DiskQual }'
RELEASE_TAG='$RELEASE_TAG'
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
printf 'Uninstaller:  %s\n' "sirgon-diskqual-uninstall"
printf 'Version:      %s\n' "$VERSION_OUTPUT"
if [ -n "$RELEASE_TAG" ]; then
    printf 'Release:      %s\n' "$RELEASE_TAG"
fi
echo
echo "Recommended first checks:"
echo "  diskqual --version"
echo "  diskqual inventory"
echo "  sirgon-diskqual-ui"
echo
echo "To uninstall while preserving reports:"
echo "  sudo sirgon-diskqual-uninstall"
