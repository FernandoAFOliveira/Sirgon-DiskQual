# wipe.py
"""Selected-drive metadata cleanup for reused disks.

This intentionally removes partition/filesystem/RAID metadata only. It is not
presented as a secure data-erasure function. Each destructive command resolves
and verifies the selected serial again immediately before touching the device.

Linux MD arrays are handled conservatively: DiskQual will stop an assembled MD
array only when every physical member disk belongs to the operator's current
selection and the array is neither mounted nor active swap.
"""

import json
import os
import subprocess
from pathlib import Path

from .cli import discover
from .devices import has_existing_layout
from .station import active_serials

BASE = Path(os.environ.get('DISKQUAL_HOME', '/opt/diskqual'))
SELECTION = BASE / 'operator' / 'selection.json'
EDGE_BYTES = 32 * 1024 * 1024


def _run(command):
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        detail = (result.stderr or result.stdout or '').strip()
        raise RuntimeError(f"{' '.join(command)} failed: {detail or result.returncode}")
    return result.stdout.strip()


def _selected_serials():
    try:
        data = json.loads(SELECTION.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f'Unable to read operator selection: {exc}') from exc
    serials = data.get('serials', []) if isinstance(data, dict) else []
    serials = [str(serial) for serial in serials if serial]
    if not serials:
        raise RuntimeError('No drives are selected.')
    return serials


def _resolve(serial):
    matches = [drive for drive in discover() if str(drive.get('serial')) == serial]
    if len(matches) != 1:
        if not matches:
            raise RuntimeError(f'{serial}: selected drive is no longer present')
        raise RuntimeError(f'{serial}: drive identity is ambiguous')
    drive = matches[0]
    dev = str(drive.get('dev') or '')
    if not dev.startswith('/dev/'):
        raise RuntimeError(f'{serial}: invalid resolved device path {dev!r}')
    return drive


def _verified_dev(serial):
    drive = _resolve(serial)
    if str(drive.get('serial')) != serial:
        raise RuntimeError(f'{serial}: identity verification failed')
    return str(drive['dev'])


def _block_rows(dev):
    result = subprocess.run(
        ['lsblk', '-nrpo', 'NAME,TYPE', dev],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout or '').strip()
        raise RuntimeError(f'Unable to inspect block topology for {dev}: {detail or result.returncode}')
    rows = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            rows.append((parts[0], parts[1]))
    return rows


def _partitions(dev):
    return [name for name, kind in _block_rows(dev) if kind == 'part']


def _holders(dev):
    paths = [dev, *_partitions(dev)]
    holders = set()
    for path in paths:
        holder_dir = Path('/sys/class/block') / Path(path).name / 'holders'
        if not holder_dir.exists():
            continue
        for holder in holder_dir.iterdir():
            if holder.name.startswith('md'):
                holders.add('/dev/' + holder.name)
    return holders


def _member_disks(md_dev):
    result = subprocess.run(
        ['lsblk', '-srnpo', 'NAME,TYPE', md_dev],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout or '').strip()
        raise RuntimeError(f'Unable to inspect members of {md_dev}: {detail or result.returncode}')
    members = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == 'disk':
            members.add(parts[0])
    return members


def _mounted_descendants(dev):
    result = subprocess.run(
        ['lsblk', '-nrpo', 'NAME,MOUNTPOINTS', dev],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    mounted = []
    for line in result.stdout.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[1].strip():
            mounted.append(f'{parts[0]} -> {parts[1].strip()}')
    return mounted


def _active_swap_devices():
    result = subprocess.run(
        ['swapon', '--noheadings', '--raw', '--output', 'NAME'],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _prepare_md_arrays(serials):
    """Stop MD arrays composed entirely of selected disks, otherwise refuse."""
    selected = {serial: _verified_dev(serial) for serial in serials}
    selected_devs = set(selected.values())
    arrays = set()
    for dev in selected_devs:
        arrays.update(_holders(dev))

    if not arrays:
        return

    swaps = _active_swap_devices()
    for md_dev in sorted(arrays):
        members = _member_disks(md_dev)
        if not members:
            raise RuntimeError(f'{md_dev}: assembled MD array has no resolvable physical members')
        outside = members - selected_devs
        if outside:
            raise RuntimeError(
                f'{md_dev}: refusing to stop RAID array because not every member disk is selected; '
                f'unselected member(s): {", ".join(sorted(outside))}'
            )
        mounted = _mounted_descendants(md_dev)
        if mounted:
            raise RuntimeError(f'{md_dev}: refusing to stop mounted RAID array: {"; ".join(mounted)}')
        if md_dev in swaps:
            raise RuntimeError(f'{md_dev}: refusing to stop RAID array because it is active swap')

    for md_dev in sorted(arrays):
        print(f'{md_dev}: stopping assembled Linux MD array; all physical members are selected')
        _run(['mdadm', '--stop', md_dev])

    subprocess.run(['udevadm', 'settle'], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _clear_partition_signatures(serial):
    """Remove MD/filesystem signatures from the selected disk's current partitions."""
    dev = _verified_dev(serial)
    partitions = _partitions(dev)
    for part in partitions:
        examine = subprocess.run(
            ['mdadm', '--examine', part],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if examine.returncode == 0:
            print(f'{serial}: clearing Linux MD superblock on {part}')
            _run(['mdadm', '--zero-superblock', '--force', part])
        print(f'{serial}: removing filesystem/signature metadata on {part}')
        _run(['wipefs', '-a', '-f', part])


def _zero_front(serial):
    dev = _verified_dev(serial)
    _run(['dd', 'if=/dev/zero', f'of={dev}', 'bs=1M', 'count=32', 'conv=fsync'])


def _zero_back(serial):
    dev = _verified_dev(serial)
    sectors_text = _run(['blockdev', '--getsz', dev])
    sectors = int(sectors_text or 0)
    count = EDGE_BYTES // 512
    if sectors <= count:
        raise RuntimeError(f'{serial}: drive is unexpectedly too small for metadata cleanup')
    start = sectors - count
    dev = _verified_dev(serial)
    _run(['dd', 'if=/dev/zero', f'of={dev}', 'bs=512', f'seek={start}', f'count={count}', 'conv=fsync'])


def _layout_diagnostics(dev):
    lsblk = subprocess.run(
        ['lsblk', '-nrpo', 'NAME,TYPE,FSTYPE,PTTYPE', dev],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout.strip()
    wipefs = subprocess.run(
        ['wipefs', '-n', dev],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout.strip()
    holders = ', '.join(sorted(_holders(dev)))
    detail = '; '.join(part for part in (lsblk, wipefs, f'holders={holders}' if holders else '') if part)
    return detail or 'layout still detected but no signature details were returned'


def wipe_serial(serial):
    if serial in active_serials():
        raise RuntimeError(f'{serial}: drive has an active DiskQual test')

    _clear_partition_signatures(serial)

    dev = _verified_dev(serial)
    print(f'{serial}: removing filesystem/RAID signatures on {dev}')
    _run(['wipefs', '-a', '-f', dev])

    dev = _verified_dev(serial)
    print(f'{serial}: zapping GPT/MBR structures on {dev}')
    _run(['sgdisk', '--zap-all', dev])

    print(f'{serial}: clearing first 32 MiB')
    _zero_front(serial)
    print(f'{serial}: clearing last 32 MiB')
    _zero_back(serial)

    dev = _verified_dev(serial)
    subprocess.run(['blockdev', '--rereadpt', dev], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(['partprobe', dev], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(['udevadm', 'settle'], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    dev = _verified_dev(serial)
    if _holders(dev):
        raise RuntimeError(f'{serial}: metadata cleanup completed but an MD holder reappeared on {dev}: {_layout_diagnostics(dev)}')
    if has_existing_layout(dev):
        raise RuntimeError(f'{serial}: metadata cleanup finished writing, but the OS still detects a layout on {dev}: {_layout_diagnostics(dev)}')

    print(f'{serial}: metadata cleanup complete and layout verification passed')


def main():
    if os.geteuid() != 0:
        raise SystemExit('Metadata wipe helper must run as root.')
    serials = _selected_serials()
    active = active_serials() & set(serials)
    if active:
        raise SystemExit('Refusing metadata wipe; active tests: ' + ', '.join(sorted(active)))

    # Stop any currently assembled arrays before touching member metadata.  Run
    # the same check again before each physical disk because udev/mdadm may
    # automatically reassemble a degraded array from members that have not yet
    # been cleaned.
    _prepare_md_arrays(serials)
    for serial in serials:
        _prepare_md_arrays(serials)
        wipe_serial(serial)

    print(f'Metadata cleanup completed for {len(serials)} drive(s).')


if __name__ == '__main__':
    main()
