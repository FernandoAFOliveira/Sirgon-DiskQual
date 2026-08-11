# Sirgon DiskQual

**Sirgon DiskQual was born out of the practical necessity to test, evaluate, and certify hard drives before placing them into service.**

When working with batches of new, used, refurbished, or enterprise drives, a simple SMART `PASSED` result is not enough. A disk may appear healthy while still carrying warning signs such as reallocated sectors, pending sectors, uncorrectable errors, interface problems, or failures that only become visible during sustained testing.

Sirgon DiskQual turns that process into a repeatable qualification workflow. It inventories drives, performs health prechecks, runs extended SMART and destructive surface tests, tracks mixed-capacity batches in real time, records the evidence, and helps the operator turn test results into reports and physical labels.

> **Warning:** full qualification is destructive. Data on selected test drives will be overwritten.

## What Sirgon DiskQual does

- Detects eligible non-OS, unmounted drives.
- Captures baseline SMART information before destructive testing.
- Classifies drives as **PASS**, **REVIEW**, or **REJECT** during precheck.
- Automatically prevents clearly rejected drives from receiving destructive tests.
- Runs SMART short and extended self-tests.
- Performs full-surface write and verification testing.
- Tracks drives by serial number rather than relying only on `/dev/sdX` names.
- Tests mixed capacities and models concurrently.
- Shows per-drive **Current** and **Overall** progress with changing ETA.
- Shows a capacity-weighted total batch progress indicator.
- Runs qualification independently under systemd so closing SSH or restarting the display does not stop the test.
- Preserves general qualification records separately from customer-facing reports.
- Supports multiple simultaneous client report projects.
- Generates configurable physical drive labels for passed, reviewed, or rejected drives.
- Provides an interactive Textual-based operator interface.

## Linux platform target

Sirgon DiskQual currently targets modern **systemd-based Linux qualification stations**. The installer recognizes common Linux package managers including `apt`, `dnf`, `yum`, `zypper`, and `pacman`.

The qualification backend relies on standard Linux disk utilities including `smartctl`, `lsblk`, `blockdev`, `wipefs`, `badblocks`, `sgdisk`, and `dd`. The installer installs the expected packages when possible and verifies that every required command is available before completing installation.

Python 3.10 or newer is required.

## Qualification workflow

A full qualification currently follows these stages:

1. Baseline SMART capture
2. SMART short self-test
3. SMART extended self-test
4. Destructive full-surface write
5. Full-surface verification
6. Final SMART capture
7. Final classification

The operator can inspect an individual drive at any time without interrupting the test engine.

## Result philosophy

**PASS** means the drive completed the applicable checks without a condition that currently requires operator intervention.

**REVIEW** means the drive is not automatically condemned, but its history or SMART data deserves human review before deployment.

**REJECT** means the baseline evidence is already strong enough that destructive qualification should not be wasted on the drive.

The exact policy will continue to evolve as more drive families and SMART formats are validated.

## Client reports

The general Sirgon DiskQual history remains the complete technical record of what was tested.

Client reports are intentionally separate. An operator can create several report projects at the same time—for example Client A, Client B, and Client C—and assign only selected qualified drives to each report. A customer therefore receives information about the drives chosen for that customer, not the entire internal test inventory.

## Labels

Sirgon DiskQual supports selectable label generation and configurable media dimensions. The current reference media is:

- DYMO 30323
- 4.000 × 2.125 inches

The design goal is to support both generated PDFs and direct printing through CUPS when a compatible printer is configured.

## Install — no repository clone required

A normal user only needs the small `install.sh` bootstrap script. The installer obtains the application package from the latest published GitHub Release automatically.

Download `install.sh` from this repository, then run:

```bash
chmod +x install.sh
sudo ./install.sh
```

The installer then:

- detects the Linux package manager;
- records which required system packages were absent before installation;
- installs Python and the required disk utilities;
- verifies Python 3.10 or newer and every external qualification command;
- verifies systemd;
- downloads the latest published Sirgon DiskQual wheel from GitHub Releases;
- installs it into `/opt/sirgon-diskqual/venv`;
- creates persistent data storage under `/opt/diskqual`;
- installs the `diskqual`, `sirgon-diskqual`, and `sirgon-diskqual-ui` commands;
- installs `sirgon-diskqual-uninstall`;
- imports the installed application modules as a sanity check; and
- records an installation manifest for safe dependency cleanup later.

If no stable release exists yet, the installer falls back to the newest published prerelease. A specific release can also be requested:

```bash
sudo ./install.sh --release v0.3.0-beta.1
```

After installation:

```bash
diskqual --version
sirgon-diskqual-ui
```

## Uninstall and clean reinstall testing

A normal uninstall removes Sirgon DiskQual itself while preserving reports, labels, client reports, logs, state, and qualification history:

```bash
sudo sirgon-diskqual-uninstall
```

For a true clean-room installer test, remove the application, purge all Sirgon DiskQual data, and remove only installer-added dependencies that the operating system determines are no longer required:

```bash
sudo sirgon-diskqual-uninstall --remove-dependencies --purge-data
```

Dependency cleanup is deliberately conservative. Packages that were already present before Sirgon DiskQual was installed are never recorded for removal, and installer-added packages are retained when the package manager reports that another installed package still needs them.

The uninstaller refuses to interrupt an active qualification by default. `--force` is available for deliberate administrative removal.

This makes the full acceptance-test cycle:

```text
clean Linux machine
    → download install.sh
    → install and verify Sirgon DiskQual
    → exercise the interface
    → complete uninstall and dependency cleanup
    → verify removal
    → reinstall from scratch
```

## Development and packaging

The Git repository is the development source, not the production runtime directory.

For development:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -e .
sirgon-diskqual-ui --demo
```

For a local package build:

```bash
python3 -m pip install build
python3 -m build
```

Tagged releases are built automatically by GitHub Actions. A tag such as `v0.3.0-beta.1` builds the wheel, validates imports and the installed command, and publishes the wheel as a GitHub prerelease asset.

## Commands

```bash
diskqual --version
diskqual inventory
diskqual quick
diskqual smart-long
diskqual monitor
diskqual report
diskqual prepare --yes
diskqual qualify --yes
sirgon-diskqual-ui
```

## Safety

Sirgon DiskQual is intended for dedicated disk-testing systems. Destructive operations should never be started until the operator has reviewed the discovered drive list and confirmed that no production or mounted disk is included.

The current implementation skips `/dev/sda` as the qualification-station OS disk and skips mounted disks, but hardware layouts differ. Treat destructive qualification as an operation requiring deliberate operator confirmation.

## Current development status

**0.3.0 Beta 1** is the first packaged prerelease intended for installation, uninstallation, portability, and end-to-end workflow testing on Linux systems before a stable release is declared.
