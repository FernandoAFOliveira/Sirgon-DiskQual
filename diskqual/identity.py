# identity.py
"""Stable drive identity helpers.

Linux /dev/sdX names are locations, not identities. DiskQual uses the drive
serial as the authoritative identity and resolves the current block-device path
immediately before operations that read from or write to a drive.
"""

import json
import re
import subprocess


def _run(command):
    return subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _smart_serial(dev):
    result = _run(['smartctl', '-i', dev])
    text = (result.stdout or '') + (result.stderr or '')
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('Serial Number:') or stripped.startswith('Serial number:'):
            return stripped.split(':', 1)[1].strip()
    return ''


def resolve_serial_device(serial):
    """Return the current /dev path for exactly one drive with *serial*.

    The lsblk serial match is verified again with smartctl before the path is
    returned. This deliberately fails closed rather than accepting a stale
    /dev/sdX path that may now refer to another disk.
    """
    serial = str(serial or '').strip()
    if not serial:
        raise RuntimeError('Drive serial is required for identity resolution')

    result = _run(['lsblk', '-J', '-d', '-o', 'NAME,TYPE,SERIAL'])
    if result.returncode:
        raise RuntimeError(f'Unable to resolve drive {serial}: lsblk failed')
    try:
        data = json.loads(result.stdout or '{}')
    except json.JSONDecodeError as exc:
        raise RuntimeError(f'Unable to resolve drive {serial}: invalid lsblk output') from exc

    candidates = []
    for row in data.get('blockdevices', []):
        if str(row.get('type') or '') != 'disk':
            continue
        if str(row.get('serial') or '').strip() == serial:
            name = str(row.get('name') or '').strip()
            if re.fullmatch(r'[A-Za-z0-9._+-]+', name):
                candidates.append('/dev/' + name)

    if len(candidates) != 1:
        if not candidates:
            raise RuntimeError(f'Drive {serial} is not currently present')
        raise RuntimeError(f'Drive serial {serial} resolved to multiple block devices')

    dev = candidates[0]
    confirmed = _smart_serial(dev)
    if confirmed != serial:
        raise RuntimeError(
            f'Drive identity mismatch: expected {serial}, but {dev} reports {confirmed or "no serial"}'
        )
    return dev
