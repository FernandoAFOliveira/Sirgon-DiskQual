# Sirgon DiskQual Installation

[← Back to README](README.md) · [Releases](https://github.com/FernandoAFOliveira/Sirgon-DiskQual/releases) · [Report an Issue](https://github.com/FernandoAFOliveira/Sirgon-DiskQual/issues)

## Linux Installation

**Recommended for most users**

You do **not** need to clone this repository, build the project, create a Python virtual environment, or download the Python wheel manually.

### Download the Linux installer

**[Open Sirgon DiskQual Releases](https://github.com/FernandoAFOliveira/Sirgon-DiskQual/releases)**

Open the newest release and download the Linux installer asset. Installer filenames include the release version, for example:

```text
sirgon-diskqual-installer-v0.3.0-beta.8.sh
```

There is only one installer asset per release. The versioned filename makes it safe to keep multiple downloaded installers without overwriting older copies.

> While Sirgon DiskQual is in prerelease testing, the link above opens the Releases page because GitHub's `latest` release redirect is intended for the latest normal release. After the first stable release, this guide will use GitHub's permanent latest-release link.

Then open a terminal in the folder where you downloaded the installer and run, replacing the filename with the version you downloaded:

```bash
chmod +x sirgon-diskqual-installer-v0.3.0-beta.8.sh
sudo ./sirgon-diskqual-installer-v0.3.0-beta.8.sh
```

The installer connects to the Sirgon DiskQual GitHub Releases page, downloads the application package, installs required Linux dependencies when possible, creates the managed application environment, and verifies the installation.

> **Important:** Sirgon DiskQual performs destructive drive qualification tests. Installation itself does not erase drives. The currently running operating-system disk and virtual block devices are excluded from qualification. Unmounted disks containing existing partitions or filesystems are shown as **PROTECTED** and are skipped by destructive qualification unless the operator deliberately uses `--allow-existing-data`.

## Supported Linux systems

Sirgon DiskQual currently targets modern **systemd-based Linux systems** using one of these package managers:

- `apt` — Debian, Ubuntu, and derivatives
- `dnf` — Fedora and modern RHEL-family distributions
- `yum` — older RHEL-family distributions
- `zypper` — openSUSE/SUSE
- `pacman` — Arch Linux and derivatives

Python 3.10 or newer is required. The installer checks the Python version automatically.

The qualification engine also requires standard Linux drive utilities including `smartctl`, `lsblk`, `blockdev`, `wipefs`, `badblocks`, `sgdisk`, and `dd`. The installer installs or verifies these tools before completing setup.

## What the installer does

The installer:

1. verifies that it is running on Linux;
2. detects the system package manager;
3. records which required packages were absent before installation;
4. installs required system packages;
5. verifies Python 3.10 or newer;
6. verifies the required disk-management utilities and systemd;
7. locates the latest published Sirgon DiskQual GitHub Release;
8. downloads the release wheel automatically;
9. creates `/opt/sirgon-diskqual/venv` for the managed application environment;
10. creates `/opt/diskqual` for persistent reports, state, labels, and qualification data;
11. installs the `diskqual`, `sirgon-diskqual`, and `sirgon-diskqual-ui` commands;
12. installs the standalone `sirgon-diskqual-uninstall` command; and
13. performs post-installation verification.

## Verify the installation

After installation, verify the installed version:

```bash
diskqual --version
```

Launch the interface without testing real drives:

```bash
sirgon-diskqual-ui --demo
```

To inventory drives without starting destructive qualification:

```bash
diskqual inventory
```

Or launch the interface and press:

```text
I  Inventory
```

## Protected disks

Sirgon DiskQual separates **visibility** from **destructive eligibility**:

- the disk backing the currently running Linux system is excluded from the qualification list;
- virtual devices such as `zram` and loop devices are ignored;
- real, unmounted disks remain visible in Inventory;
- disks containing an existing partition table or filesystem are marked **PROTECTED**.

Protected disks are not destructively qualified by default. If you intentionally want to erase and qualify those disks, use the explicit override:

```bash
diskqual qualify --yes --allow-existing-data
```

That option is intentionally verbose because it authorizes destructive testing of disks that contain existing data structures.

## Install a specific release

Normally the installer chooses the latest published release automatically. To install a specific version instead:

```bash
sudo ./sirgon-diskqual-installer-v0.3.0-beta.8.sh --release v0.3.0-beta.8
```

Available releases are listed here:

**[Sirgon DiskQual Releases](https://github.com/FernandoAFOliveira/Sirgon-DiskQual/releases)**

## Updating Sirgon DiskQual

Download the installer from the newest release and run it normally. For example:

```bash
chmod +x sirgon-diskqual-installer-v0.3.0-beta.8.sh
sudo ./sirgon-diskqual-installer-v0.3.0-beta.8.sh
```

The managed Python package is upgraded while persistent qualification data under `/opt/diskqual` is preserved.

## Uninstall

A normal uninstall removes Sirgon DiskQual while preserving reports, labels, client reports, logs, state, and qualification history:

```bash
sudo sirgon-diskqual-uninstall
```

For a complete clean-room uninstall, including Sirgon DiskQual data and installer-added dependencies that are no longer required by the operating system:

```bash
sudo sirgon-diskqual-uninstall --remove-dependencies --purge-data
```

The dependency cleanup is deliberately conservative. Packages that existed before Sirgon DiskQual was installed are not recorded for removal, and installer-added packages are retained when the package manager reports that another installed package still requires them.

The uninstaller refuses to interrupt an active qualification by default. Administrative override is available with `--force`, but it should be used only when deliberately stopping and removing an active installation.

## Clean install test

For release validation, the recommended test is:

```text
clean Linux system
    ↓
open the newest GitHub Release
    ↓
download the versioned Linux installer
    ↓
install and verify Sirgon DiskQual
    ↓
run sirgon-diskqual-ui --demo
    ↓
complete uninstall with --remove-dependencies --purge-data
    ↓
verify removal
    ↓
download/run the installer again
```

This confirms that the public installer works without requiring repository source files or a developer environment.

## Manual/developer installation

Repository cloning and local wheel installation are intended for development and troubleshooting, not normal end-user installation. Development instructions remain in the main [README](README.md).
