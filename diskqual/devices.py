# devices.py
"""Safe physical-disk discovery for Sirgon DiskQual.

The qualification station must never treat the operating-system disk or
virtual block devices as test candidates.  This module deliberately identifies
protected disks from the live Linux block-device topology instead of assuming
that the OS is installed on a particular device name such as /dev/sda.
"""

import subprocess
from pathlib import Path


VIRTUAL_PREFIXES = (
    'zram',
    'loop',
    'ram',
    'fd',
)


def _run(args):
    return subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _disk_ancestors(source):
    """Return whole-disk ancestors for a block source such as a partition/LVM LV."""
    if not source or not source.startswith('/dev/'):
        return set()
    result = _run(['lsblk', '-srnpo', 'NAME,TYPE', source])
    disks = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == 'disk':
            disks.add(parts[0])
    return disks


def protected_os_disks():
    """Find physical disks used by the running OS, boot filesystems, or swap."""
    protected = set()

    mounts = _run(['findmnt', '-rn', '-o', 'SOURCE,TARGET'])
    for line in mounts.stdout.splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        source, target = parts
        if target in ('/', '/boot', '/boot/efi'):
            protected.update(_disk_ancestors(source))

    swaps = _run(['swapon', '--noheadings', '--raw', '--output', 'NAME'])
    for source in swaps.stdout.splitlines():
        protected.update(_disk_ancestors(source.strip()))

    return protected


def is_virtual_disk(dev):
    name = Path(dev).name
    if name.startswith(VIRTUAL_PREFIXES):
        return True

    # Real SATA/SAS/NVMe/USB disks normally expose a sysfs device link.  zram
    # and other synthetic block devices do not.  Keep the explicit prefix
    # checks above because they are easy to understand and robust across distros.
    sys_device = Path('/sys/class/block') / name / 'device'
    return not sys_device.exists()


def has_mounted_descendant(dev):
    """Protect a disk whenever it or one of its partitions is mounted."""
    result = _run(['lsblk', '-nrpo', 'MOUNTPOINT', dev])
    for line in result.stdout.splitlines():
        mountpoint = line.strip()
        if mountpoint and mountpoint != '[SWAP]':
            return True
    return False


def list_candidate_disks():
    """Return physical, unmounted, non-OS whole disks eligible for inspection."""
    protected = protected_os_disks()
    result = _run(['lsblk', '-dnpo', 'NAME,TYPE,SIZE'])
    candidates = []

    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        dev, dev_type, size_text = parts[:3]
        if dev_type != 'disk':
            continue
        if is_virtual_disk(dev):
            continue
        if dev in protected:
            continue
        if has_mounted_descendant(dev):
            continue

        # Avoid zero-sized placeholder devices/controllers.
        try:
            size = int(_run(['blockdev', '--getsize64', dev]).stdout.strip() or 0)
        except ValueError:
            size = 0
        if size <= 0:
            continue

        candidates.append(dev)

    return candidates
