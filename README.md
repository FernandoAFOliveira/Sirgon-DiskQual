# DiskQual v0.1

Disk qualification utility for the Dell R510 disk testing station.

## Install on the R510

```bash
sudo apt update
sudo apt install -y smartmontools gdisk util-linux python3
cd diskqual
chmod +x install.sh diskqual-run
./install.sh
```

## Commands

```bash
diskqual inventory       # detect non-OS disks and save SMART reports
diskqual quick           # SMART short tests, wait, report
diskqual smart-long      # launch SMART extended tests
diskqual monitor         # full-screen terminal dashboard
diskqual report          # save current SMART summary
diskqual prepare --yes   # destructive signature wipe for test drives
```

## Current safety rules

- Skips mounted disks.
- Skips `/dev/sda` by default for the R510 OS disk.
- Tracks drives by serial number in reports.
- `prepare --yes` is destructive. Use only for drives intended for testing.

## Recommended next step for your current batch

```bash
diskqual inventory
diskqual monitor
```

Backup write/read qualification will be added in v0.2 after dashboard validation.
