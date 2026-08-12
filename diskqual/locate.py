# locate.py
import argparse
import json
import os
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


def locate_capability(dev):
    files = _candidate_locate_files(dev)
    if files:
        return {'supported': True, 'method': 'Linux enclosure services', 'path': str(files[0])}
    return {
        'supported': False,
        'method': None,
        'path': None,
        'message': 'Drive identification is not supported by this controller/enclosure through Linux enclosure services.',
    }


def set_locate(dev, enabled):
    capability = locate_capability(dev)
    if not capability['supported']:
        return False, capability['message']
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
            print(f'Locate supported for {dev}: {capability["method"]}')
            return
        print(capability['message'])
        raise SystemExit(2)
    ok, message = set_locate(dev, args.action == 'on')
    print(message)
    if not ok:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
