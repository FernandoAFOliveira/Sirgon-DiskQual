#!/bin/bash
set -euo pipefail
sudo mkdir -p /opt/diskqual/app
sudo cp -r diskqual /opt/diskqual/app/
sudo install -m 755 diskqual-run /usr/local/bin/diskqual
sudo mkdir -p /opt/diskqual/{reports,logs,inventory}
sudo chown -R tony:tony /opt/diskqual || true
echo "Installed. Try: diskqual inventory"
