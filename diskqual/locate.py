# locate.py
import argparse
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

BASE = Path(os.environ.get('DISKQUAL_HOME', '/opt/diskqual'))
REQUEST = BASE / 'operator' / 'locate.json'


def _selected_device():
    try:
        data = json.loads(REQUEST.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f'Unable to read locate request: {exc}') from exc
    dev = str(data.get('dev') or '')
    if not dev.startswith('/dev/'):
        raise RuntimeError('Locate request does not contain a valid block device.')
    return dev


def _candidate_locate_files(dev):
    name = Path(dev).name
    block = Path('/sys/class/block') / name
    if not block.exists():
        return []

    candidates = []
    device = block / 'device'
    try:
        resolved = device.resolve()
    except OSError:
        resolved = device

    for parent in [resolved, *resolved.parents]:
        for pattern in ('enclosure_device:*', 'enclosure_device*'):
            for entry in parent.glob(pattern):
                locate = entry / 'locate'
                if locate.exists():
                    candidates.append(locate)

    enclosure_root = Path('/sys/class/enclosure')
    if enclosure_root.exists():
        for locate in enclosure_root.glob('*/*/locate'):
            slot = locate.parent
            block_dir = slot / 'device' / 'block'
            if (block_dir / name).exists():
                candidates.append(locate)

    unique = []
    seen = set()
    for path in candidates:
        text = str(path)
        if text not in seen:
            seen.add(text)
            unique.append(path)
    return unique


def _run(command):
    return subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _device_serial(dev):
    result = _run(['lsblk', '-dn', '-o', 'SERIAL', dev])
    if result.returncode:
        return ''
    return result.stdout.strip()


def _perccli_path():
    candidates = [
        shutil.which('perccli'),
        '/usr/local/bin/perccli',
        '/opt/MegaRAID/perccli/perccli64',
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(candidate)
    return None


def _perc_controllers(binary):
    result = _run([binary, 'show'])
    if result.returncode:
        return []
    controllers = []
    in_overview = False
    for line in result.stdout.splitlines():
        if 'System Overview' in line:
            in_overview = True
            continue
        if not in_overview:
            continue
        match = re.match(r'^\s*(\d+)\s+\S+', line)
        if match:
            controllers.append(int(match.group(1)))
    return sorted(set(controllers))


def _perc_slots(binary, controller):
    result = _run([binary, f'/c{controller}', '/eall', '/sall', 'show'])
    if result.returncode:
        return []
    slots = []
    for line in result.stdout.splitlines():
        match = re.match(r'^\s*(\d+):(\d+)\s+', line)
        if match:
            slots.append((int(match.group(1)), int(match.group(2))))
    return sorted(set(slots))


def _normalized_serial(value):
    return re.sub(r'[^A-Za-z0-9]', '', str(value or '')).upper()


def _perc_slot_serial(binary, controller, enclosure, slot):
    result = _run([binary, f'/c{controller}', f'/e{enclosure}', f'/s{slot}', 'show', 'all'])
    if result.returncode:
        return ''
    patterns = (
        r'^\s*SN\s*=\s*(.+?)\s*$',
        r'^\s*Serial Number\s*=\s*(.+?)\s*$',
        r'^\s*Serial Number\s*:\s*(.+?)\s*$',
    )
    for line in result.stdout.splitlines():
        for pattern in patterns:
            match = re.match(pattern, line, re.I)
            if match:
                return match.group(1).strip()
    return ''


def _perc_location(dev):
    binary = _perccli_path()
    if not binary:
        return None
    serial = _device_serial(dev)
    wanted = _normalized_serial(serial)
    if not wanted:
        return None

    for controller in _perc_controllers(binary):
        for enclosure, slot in _perc_slots(binary, controller):
            candidate = _perc_slot_serial(binary, controller, enclosure, slot)
            if candidate and _normalized_serial(candidate) == wanted:
                return {
                    'binary': binary,
                    'controller': controller,
                    'enclosure': enclosure,
                    'slot': slot,
                    'serial': serial,
                }
    return None


def locate_capability(dev):
    files = _candidate_locate_files(dev)
    if files:
        return {
            'supported': True,
            'method': 'Linux enclosure services',
            'path': str(files[0]),
        }

    perc = _perc_location(dev)
    if perc:
        return {
            'supported': True,
            'method': 'Dell PERC CLI',
            'path': None,
            'perc': perc,
            'message': (
                f"PERC controller {perc['controller']}, enclosure {perc['enclosure']}, "
                f"slot {perc['slot']}"
            ),
        }

    if _perccli_path():
        message = (
            'PERC CLI is available, but DiskQual could not safely map this Linux block device '
            'to a PERC enclosure slot by serial number.'
        )
    else:
        message = (
            'Drive identification is not supported by this controller/enclosure through Linux '
            'enclosure services, and PERC CLI is not available.'
        )
    return {'supported': False, 'method': None, 'path': None, 'message': message}


def _set_perc_locate(perc, enabled):
    action = ['start', 'locate'] if enabled else ['stop', 'locate']
    command = [
        perc['binary'],
        f"/c{perc['controller']}",
        f"/e{perc['enclosure']}",
        f"/s{perc['slot']}",
        *action,
    ]
    result = _run(command)
    if result.returncode or 'Status = Success' not in result.stdout:
        detail = (result.stderr or result.stdout or 'PERC CLI locate command failed').strip()
        return False, detail.splitlines()[-1]
    state = 'ON' if enabled else 'OFF'
    return True, (
        f"Locate LED {state} for {perc['serial']} using Dell PERC CLI "
        f"(c{perc['controller']} e{perc['enclosure']} s{perc['slot']})."
    )


def set_locate(dev, enabled):
    capability = locate_capability(dev)
    if not capability['supported']:
        return False, capability['message']

    if capability['method'] == 'Dell PERC CLI':
        return _set_perc_locate(capability['perc'], enabled)

    path = Path(capability['path'])
    try:
        path.write_text('1' if enabled else '0')
    except OSError as exc:
        return False, f'Unable to control the drive identify LED: {exc}'
    state = 'ON' if enabled else 'OFF'
    return True, f'Locate LED {state} for {dev} using {capability["method"]}.'


def main():
    parser = argparse.ArgumentParser(prog='Sirgon DiskQual locate helper')
    parser.add_argument('action', choices=('on', 'off', 'check'))
    args = parser.parse_args()
    dev = _selected_device()
    if args.action == 'check':
        capability = locate_capability(dev)
        if capability['supported']:
            detail = capability.get('message')
            suffix = f' ({detail})' if detail else ''
            print(f'Locate supported for {dev}: {capability["method"]}{suffix}')
            return
        print(capability['message'])
        raise SystemExit(2)
    ok, message = set_locate(dev, args.action == 'on')
    print(message)
    if not ok:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
