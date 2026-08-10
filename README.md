# DiskQual v0.2 development

Disk qualification utility for the Dell R510 disk testing station.

## Install on the R510

```bash
sudo apt update
sudo apt install -y smartmontools gdisk util-linux e2fsprogs python3
cd diskqual
chmod +x install.sh diskqual-run
./install.sh
```

## Commands

```bash
diskqual inventory       # detect non-OS/unmounted disks and save SMART reports
diskqual quick           # SMART short tests, wait, report
diskqual smart-long      # launch SMART extended tests
diskqual monitor         # live full-screen batch dashboard
diskqual report          # save current SMART summary
diskqual prepare --yes   # destructive signature wipe for test drives
diskqual qualify --yes   # destructive full qualification with live dashboard
```

## Live qualification dashboard

`diskqual qualify --yes` discovers all eligible test drives and tests mixed sizes/models independently and concurrently. Each drive has two human-readable progress bars:

- **Current**: percentage through the operation being performed now.
- **Overall**: weighted percentage through the complete qualification workflow.

The top of the screen also shows a size-weighted **TOTAL BATCH** progress bar so a batch containing different-capacity disks does not treat a 500 GB disk as equivalent work to a 4 TB disk.

Each drive line shows its device, serial, capacity, current stage, current percentage, total percentage, elapsed time, changing ETA, and measured throughput when available. ETAs intentionally recalculate as observed performance changes.

The qualification stages are:

1. Baseline SMART capture
2. SMART short self-test
3. SMART extended self-test
4. Destructive full-surface 0x00 write
5. Full-surface read/verify
6. Final SMART capture
7. PASS / REVIEW / BAD classification

Progress state is atomically written to `/opt/diskqual/state.json`. A second terminal can display it at any time with:

```bash
diskqual monitor
```

Qualification logs and before/after SMART data are stored under `/opt/diskqual/reports/qualify_<timestamp>/`.

## Safety rules

- `/dev/sda` is skipped by default as the R510 OS disk.
- Mounted disks are skipped.
- Drives are tracked by serial number, not only `/dev/sdX` names.
- `prepare --yes` and `qualify --yes` are destructive.
- Always review `diskqual inventory` before beginning a destructive qualification batch.

## Current development note

This feature is on `feature/progress-dashboard` for validation on the Dell R510 before merging into `main`.
