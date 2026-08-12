#!/usr/bin/env bash
# uninstall.sh
set -euo pipefail

APP_NAME="Sirgon DiskQual"
APP_ROOT="/opt/sirgon-diskqual"
DATA_ROOT="/opt/diskqual"
MANIFEST="$APP_ROOT/install-manifest.env"
REMOVE_DEPENDENCIES=0
PURGE_DATA=0
FORCE=0

info() { printf '[INFO] %s\n' "$*"; }
ok() { printf '[ OK ] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*" >&2; }
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Usage: sudo sirgon-diskqual-uninstall [options]

Options:
  --remove-dependencies  Remove only system packages that Sirgon DiskQual
                         installed and that are no longer required elsewhere.
  --purge-data           Also delete /opt/diskqual reports, labels, state,
                         client reports, logs, and qualification history.
  --force                Continue even if a DiskQual test service appears active.
  -h, --help             Show this help.

Default behavior removes the application but preserves qualification data and
leaves system packages installed.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --remove-dependencies) REMOVE_DEPENDENCIES=1 ;;
        --purge-data) PURGE_DATA=1 ;;
        --force) FORCE=1 ;;
        -h|--help) usage; exit 0 ;;
        *) fail "Unknown option: $1" ;;
    esac
    shift
done

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
    fail "Run this uninstaller as root, for example: sudo sirgon-diskqual-uninstall"
fi

if [ "$(uname -s)" != "Linux" ]; then
    fail "$APP_NAME uninstall currently supports Linux systems."
fi

TEST_SERVICES=(diskqual-qualify.service diskqual-smart-long.service diskqual-surface.service)
if command -v systemctl >/dev/null 2>&1; then
    if [ "$FORCE" -ne 1 ]; then
        for service in "${TEST_SERVICES[@]}"; do
            if systemctl is-active --quiet "$service" 2>/dev/null; then
                fail "A DiskQual test phase is active ($service). Let it finish, or rerun with --force if you intentionally want to stop it."
            fi
        done
    fi

    for service in diskqual-monitor.service "${TEST_SERVICES[@]}"; do
        if systemctl is-active --quiet "$service" 2>/dev/null; then
            info "Stopping $service..."
            systemctl stop "$service" || true
        fi
        if systemctl is-enabled --quiet "$service" 2>/dev/null; then
            systemctl disable "$service" || true
        fi
    done
fi

PACKAGE_MANAGER=""
INSTALLED_BY_SIRGON=""
if [ -f "$MANIFEST" ]; then
    # The installer writes only shell-safe values to this root-owned file.
    # shellcheck disable=SC1090
    . "$MANIFEST"
    ok "Loaded installation manifest"
else
    warn "Installation manifest not found. Application files can still be removed, but dependency cleanup will be skipped."
fi

info "Removing Sirgon DiskQual command launchers..."
rm -f /usr/local/bin/diskqual /usr/local/bin/sirgon-diskqual /usr/local/bin/sirgon-diskqual-ui
rm -f /etc/profile.d/sirgon-diskqual.sh
rm -f /etc/sudoers.d/sirgon-diskqual-inventory /etc/sudoers.d/sirgon-diskqual-operator

for unit in /etc/systemd/system/diskqual-monitor.service /etc/systemd/system/diskqual-qualify.service /etc/systemd/system/diskqual-smart-long.service /etc/systemd/system/diskqual-surface.service; do
    [ -e "$unit" ] && rm -f "$unit"
done
if command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload || true
fi

if [ -d "$APP_ROOT" ]; then
    info "Removing managed application environment: $APP_ROOT"
    rm -rf "$APP_ROOT"
    ok "Application files removed"
else
    ok "Application directory already absent"
fi

remove_recorded_dependencies() {
    [ -n "${INSTALLED_BY_SIRGON:-}" ] || {
        warn "No packages were recorded as installed by Sirgon DiskQual."
        return
    }

    info "Considering installer-added packages for safe removal: $INSTALLED_BY_SIRGON"

    case "${PACKAGE_MANAGER:-}" in
        apt)
            for pkg in $INSTALLED_BY_SIRGON; do
                if dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q 'install ok installed'; then
                    apt-mark auto "$pkg" >/dev/null || true
                fi
            done
            DEBIAN_FRONTEND=noninteractive apt-get autoremove -y
            ;;
        dnf|yum|zypper)
            for pkg in $INSTALLED_BY_SIRGON; do
                rpm -q "$pkg" >/dev/null 2>&1 || continue
                if rpm -q --whatrequires "$pkg" 2>/dev/null | grep -qv '^no package requires'; then
                    warn "Keeping $pkg because another installed RPM package requires it."
                    continue
                fi
                case "$PACKAGE_MANAGER" in
                    dnf) dnf remove -y "$pkg" ;;
                    yum) yum remove -y "$pkg" ;;
                    zypper) zypper --non-interactive remove "$pkg" ;;
                esac
            done
            ;;
        pacman)
            for pkg in $INSTALLED_BY_SIRGON; do
                pacman -Q "$pkg" >/dev/null 2>&1 || continue
                if pacman -Qtdq 2>/dev/null | grep -Fxq "$pkg"; then
                    pacman -Rns --noconfirm "$pkg"
                else
                    warn "Keeping $pkg because it is not currently an orphan."
                fi
            done
            ;;
        *)
            warn "Unknown or missing package-manager record; dependency cleanup skipped."
            ;;
    esac
}

if [ "$REMOVE_DEPENDENCIES" -eq 1 ]; then
    remove_recorded_dependencies
else
    info "System dependencies preserved. Use --remove-dependencies for safe orphan cleanup."
fi

if [ "$PURGE_DATA" -eq 1 ]; then
    if [ -d "$DATA_ROOT" ]; then
        warn "Purging all Sirgon DiskQual data from $DATA_ROOT"
        rm -rf "$DATA_ROOT"
    fi
    ok "Persistent data removed"
else
    if [ -d "$DATA_ROOT" ]; then
        ok "Persistent qualification data preserved at $DATA_ROOT"
    fi
fi

# Remove the installed copy last. A running shell script can safely unlink itself.
rm -f /usr/local/sbin/sirgon-diskqual-uninstall

echo
printf '%s has been uninstalled.\n' "$APP_NAME"
if [ "$PURGE_DATA" -eq 0 ]; then
    echo "Reports and qualification data were preserved."
fi
if [ "$REMOVE_DEPENDENCIES" -eq 0 ]; then
    echo "System packages were preserved."
fi
