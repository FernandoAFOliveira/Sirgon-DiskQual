# wipe.py
"""Selected-drive metadata cleanup for reused disks.

This intentionally removes partition/filesystem/RAID metadata only. It is not
presented as a secure data-erasure function. Each destructive command resolves
and verifies the selected serial again immediately before touching the device.
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
    detail = '; '.join(part for part in (lsblk, wipefs) if part)
    return detail or 'layout still detected but no signature details were returned'


def wipe_serial(serial):
    if serial in active_serials():
        raise RuntimeError(f'{serial}: drive has an active DiskQual test')

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
    for serial in serials:
        wipe_serial(serial)
    print(f'Metadata cleanup completed for {len(serials)} drive(s).')


if __name__ == '__main__':
    main()
