# reset.py
"""Reset selected drives to a fresh qualification state while preserving history."""

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .cli import discover
from .station import active_serials

BASE = Path(os.environ.get('DISKQUAL_HOME', '/opt/diskqual'))
SELECTION = BASE / 'operator' / 'selection.json'
WORKFLOWS = BASE / 'workflow-drives'
HISTORY = BASE / 'history' / 'qualification-resets'


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


def _present_serials():
    return {str(drive.get('serial')) for drive in discover() if drive.get('serial')}


def reset_serial(serial):
    if serial in active_serials():
        raise RuntimeError(f'{serial}: drive has an active DiskQual test')
    if serial not in _present_serials():
        raise RuntimeError(f'{serial}: selected drive is no longer present')

    workflow = WORKFLOWS / f'{serial}.json'
    if workflow.exists():
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        HISTORY.mkdir(parents=True, exist_ok=True)
        destination = HISTORY / f'{serial}-{stamp}.json'
        shutil.copy2(workflow, destination)
        workflow.unlink()
        print(f'{serial}: previous qualification state archived to {destination}')
    else:
        print(f'{serial}: no persistent qualification state existed')

    print(f'{serial}: qualification state reset; drive may start again with SMART Long')


def main():
    if os.geteuid() != 0:
        raise SystemExit('Qualification reset helper must run as root.')
    try:
        serials = _selected_serials()
        active = active_serials() & set(serials)
        if active:
            raise RuntimeError('Refusing qualification reset; active tests: ' + ', '.join(sorted(active)))
        for serial in serials:
            reset_serial(serial)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    print(f'Qualification state reset for {len(serials)} drive(s).')


if __name__ == '__main__':
    main()
