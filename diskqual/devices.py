# devices.py
"""Safe physical-disk discovery for Sirgon DiskQual.

Inventory should show real physical disks that an operator may need to inspect,
while destructive qualification must never silently treat the running OS disk,
virtual block devices, or disks containing an existing data layout as disposable.
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


def has_existing_layout(dev):
    """Return True when a disk contains partitions or recognizable filesystems.

    An inactive dual-boot OS disk is intentionally visible in Inventory, but an
    existing partition table/filesystem is enough to require an explicit
    destructive override before qualification.
    """
    result = _run(['lsblk', '-nrpo', 'NAME,TYPE,FSTYPE,PTTYPE', dev])
    lines = [line.split() for line in result.stdout.splitlines() if line.strip()]
    for parts in lines:
        if len(parts) >= 2 and parts[1] == 'part':
            return True
        if len(parts) >= 3 and parts[2] not in ('', '-'):
            return True
        if len(parts) >= 4 and parts[3] not in ('', '-'):
            return True
    return False


def list_candidate_disks():
    """Return real physical disks visible to Sirgon DiskQual Inventory.

    The running OS disk and virtual devices are omitted entirely. Unmounted
    physical disks remain visible even when they contain an existing layout;
    precheck marks those disks PROTECTED so destructive qualification skips
    them unless the operator explicitly allows existing data to be destroyed.
    """
    protected = protected_os_disks()
    result = _run(['lsblk', '-dnpo', 'NAME,TYPE,SIZE'])
    candidates = []

    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        dev, dev_type, _size_text = parts[:3]
        if dev_type != 'disk':
            continue
        if is_virtual_disk(dev):
            continue
        if dev in protected:
            continue
        if has_mounted_descendant(dev):
            continue

        try:
            size = int(_run(['blockdev', '--getsize64', dev]).stdout.strip() or 0)
        except ValueError:
            size = 0
        if size <= 0:
            continue

        candidates.append(dev)

    return candidates
