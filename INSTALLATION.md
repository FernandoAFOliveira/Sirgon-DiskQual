# Sirgon DiskQual Installation

[← Back to README](README.md) · [Latest Release](https://github.com/FernandoAFOliveira/Sirgon-DiskQual/releases) · [Report an Issue](https://github.com/FernandoAFOliveira/Sirgon-DiskQual/issues)

## Linux Installation

**Recommended for most users**

You do **not** need to clone this repository, build the project, create a Python virtual environment, or download the Python wheel manually.

### Download the Linux installer

**[Download the latest Sirgon DiskQual Linux installer](https://github.com/FernandoAFOliveira/Sirgon-DiskQual/releases/download/v0.3.0-beta.2/sirgon-diskqual-installer.sh)**

The download should start immediately. Save the file as:

```text
sirgon-diskqual-installer.sh
```

Then open a terminal in the folder where you downloaded it and run:

```bash
chmod +x sirgon-diskqual-installer.sh
sudo ./sirgon-diskqual-installer.sh
```

The installer connects to the Sirgon DiskQual GitHub Releases page, downloads the application package, installs required Linux dependencies when possible, creates the managed application environment, and verifies the installation.

> **Important:** Sirgon DiskQual performs destructive drive qualification tests. Installation itself does not erase drives, but commands such as `diskqual qualify --yes` are destructive. Review the detected drives carefully before beginning a qualification run.

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

## Install a specific release

Normally the installer chooses the latest published release automatically. To install a specific version instead:

```bash
sudo ./sirgon-diskqual-installer.sh --release v0.3.0-beta.2
```

Available releases are listed here:

**[Sirgon DiskQual Releases](https://github.com/FernandoAFOliveira/Sirgon-DiskQual/releases)**

## Updating Sirgon DiskQual

Download the current installer again and run it normally:

```bash
chmod +x sirgon-diskqual-installer.sh
sudo ./sirgon-diskqual-installer.sh
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
download sirgon-diskqual-installer.sh
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
