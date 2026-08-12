# smart_observe.py
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from .cli import smart_text
from .progress import atomic_write_json

BASE = Path(os.environ.get('DISKQUAL_HOME', '/opt/diskqual'))
DRIVES = BASE / 'drives.json'
OBSERVED = BASE / 'operator' / 'smart-observed.json'


def _load_drives():
    try:
        data = json.loads(DRIVES.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _remaining_percent(text):
    match = re.search(r'(\d+)\s*%\s+of test remaining', text, re.I)
    if not match:
        return None
    return max(0, min(100, int(match.group(1))))


def _extended_minutes(text):
    patterns = (
        r'Extended self-test routine\s+recommended polling time:\s*\(\s*(\d+)\s*\)\s*minutes',
        r'Extended self-test routine recommended polling time:\s*\(\s*(\d+)\s*\)\s*minutes',
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I | re.S)
        if match:
            return int(match.group(1))
    return None


def observe_drive(drive):
    dev = str(drive.get('dev') or '')
    serial = str(drive.get('serial') or drive.get('id') or '')
    if not dev or not serial:
        return None

    text = smart_text(dev, ['-c'])
    remaining = _remaining_percent(text)
    estimate_minutes = _extended_minutes(text)
    running = remaining is not None and 'self-test routine in progress' in text.lower()

    row = {
        'serial': serial,
        'dev': dev,
        'observed_utc': datetime.now(timezone.utc).isoformat(),
        'smart_long_running': running,
    }
    if running:
        progress = (100 - remaining) / 100.0
        row.update({
            'remaining_percent': remaining,
            'stage': 'smart-long',
            'stage_progress': progress,
            'message': f'SMART extended self-test in progress — {remaining}% remaining',
        })
        if estimate_minutes is not None:
            row['recommended_minutes'] = estimate_minutes
            row['stage_eta_seconds'] = int(estimate_minutes * 60 * remaining / 100)
    return row


def observe_all():
    rows = {}
    for drive in _load_drives():
        try:
            row = observe_drive(drive)
        except Exception as exc:
            serial = str(drive.get('serial') or drive.get('id') or '')
            if serial:
                rows[serial] = {'serial': serial, 'dev': drive.get('dev', ''), 'error': str(exc)}
            continue
        if row:
            rows[row['serial']] = row

    OBSERVED.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(OBSERVED, {
        'updated_utc': datetime.now(timezone.utc).isoformat(),
        'drives': rows,
    })
    return rows


def main():
    rows = observe_all()
    running = sum(1 for row in rows.values() if row.get('smart_long_running'))
    print(f'Observed {len(rows)} drive(s); {running} SMART self-test(s) running.')


if __name__ == '__main__':
    main()
