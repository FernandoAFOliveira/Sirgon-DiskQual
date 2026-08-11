# Sirgon DiskQual

**[Installation](INSTALLATION.md) · [Latest Release](https://github.com/FernandoAFOliveira/Sirgon-DiskQual/releases) · [Issues](https://github.com/FernandoAFOliveira/Sirgon-DiskQual/issues)**

**Sirgon DiskQual was born out of the practical necessity to test, evaluate, and certify hard drives before placing them into service.**

When working with batches of new, used, refurbished, or enterprise drives, a simple SMART `PASSED` result is not enough. A disk may appear healthy while still carrying warning signs such as reallocated sectors, pending sectors, uncorrectable errors, interface problems, or failures that only become visible during sustained testing.

Sirgon DiskQual turns that process into a repeatable qualification workflow. It inventories drives, performs health prechecks, runs extended SMART and destructive surface tests, tracks mixed-capacity batches in real time, records the evidence, and helps the operator turn test results into reports and physical labels.

> **Warning:** full qualification is destructive. Data on selected test drives will be overwritten.

## Install on Linux

Normal users do **not** need to clone this repository or build the Python package.

**[Open the Linux installation guide](INSTALLATION.md)**

The installer automatically obtains the current Sirgon DiskQual package from GitHub Releases and installs/verifies the required Linux dependencies.

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

Tagged releases are built automatically by GitHub Actions. Each release publishes:

- `sirgon-diskqual-installer.sh` — the user-facing Linux installer
- `sirgon_diskqual-<version>-py3-none-any.whl` — the Python application package

The workflow validates the built package before publishing the release.

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
