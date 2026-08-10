#!/bin/bash
# install.sh
set -euo pipefail

if [ "$#" -ne 1 ]; then
    echo "Usage: sudo ./install.sh /path/to/sirgon_diskqual-<version>-py3-none-any.whl"
    exit 2
fi

WHEEL=$(readlink -f "$1")
APP_ROOT=/opt/sirgon-diskqual
VENV="$APP_ROOT/venv"
DATA_ROOT=/opt/diskqual

if [ ! -f "$WHEEL" ]; then
    echo "Wheel not found: $WHEEL"
    exit 2
fi

apt-get update
apt-get install -y python3 python3-venv smartmontools gdisk util-linux e2fsprogs

mkdir -p "$APP_ROOT" "$DATA_ROOT"/{reports,logs,inventory,labels,client-reports}

if [ ! -x "$VENV/bin/python" ]; then
    python3 -m venv "$VENV"
fi

"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install --upgrade "$WHEEL"

ln -sfn "$VENV/bin/diskqual" /usr/local/bin/diskqual
ln -sfn "$VENV/bin/sirgon-diskqual" /usr/local/bin/sirgon-diskqual
ln -sfn "$VENV/bin/sirgon-diskqual-ui" /usr/local/bin/sirgon-diskqual-ui

cat >/etc/profile.d/sirgon-diskqual.sh <<'EOF'
export DISKQUAL_HOME=/opt/diskqual
EOF

printf 'Installed: '
DISKQUAL_HOME="$DATA_ROOT" /usr/local/bin/diskqual --version

echo "Application: $APP_ROOT"
echo "Data:        $DATA_ROOT"
echo "Command:     diskqual"
echo "TUI:         sirgon-diskqual-ui"
