# status.py
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .progress import format_duration, load_state, overall_for_drive

BASE = Path(os.environ.get('DISKQUAL_HOME', '/opt/diskqual'))
STATE = BASE / 'state.json'
STALE_AFTER_SECONDS = 120
RUNNING_STATES = {'RUNNING', 'SMART_LONG_RUNNING', 'SURFACE_RUNNING'}
WORKER_UNITS = ('diskqual-qualify.service', 'diskqual-smart-long.service', 'diskqual-surface.service')


def _run(args):
    return subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _parse_time(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def state_age_seconds(state, now=None):
    updated = _parse_time((state or {}).get('updated_utc'))
    if updated is None:
        return None
    now = now or datetime.now(timezone.utc)
    return max(0, int((now - updated).total_seconds()))


def qualification_worker_active():
    for unit in WORKER_UNITS:
        if _run(['systemctl', 'is-active', '--quiet', unit]).returncode == 0:
            return True
    result = _run(['pgrep', '-f', r'python(3)? .*diskqual\.(engine|workflow)|python(3)? -m diskqual\.(engine|workflow)'])
    return result.returncode == 0 and bool(result.stdout.strip())


def runtime_health(state):
    status = str((state or {}).get('status') or 'UNKNOWN').upper()
    age = state_age_seconds(state)
    worker = qualification_worker_active()
    stale = status in RUNNING_STATES and not worker and (age is None or age >= STALE_AFTER_SECONDS)
    return {
        'worker_active': worker,
        'age_seconds': age,
        'stale': stale,
        'display_status': 'WORKER STOPPED' if stale else status,
    }


def _size_tb(drive):
    return float(drive.get('size_bytes') or 0) / 1_000_000_000_000


def render_status(state):
    if not state:
        return 'Sirgon DiskQual — no qualification state found.'

    health = runtime_health(state)
    lines = [
        'SIRGON DISKQUAL — QUALIFICATION STATUS',
        f"Batch: {state.get('batch_id', 'unknown')}",
        f"Status: {health['display_status']}",
    ]

    if health['age_seconds'] is not None:
        lines.append(f"Last update: {format_duration(health['age_seconds'])} ago")
    lines.append(f"Worker: {'ACTIVE' if health['worker_active'] else 'NOT RUNNING'}")

    if health['stale']:
        lines.extend([
            '',
            'WARNING: State says a test phase is running, but no DiskQual worker is active.',
            'The percentages below are the last recorded values and are not advancing.',
        ])

    if str(state.get('status') or '').upper() == 'SMART_REVIEW':
        lines.extend(['', 'SMART Long phase complete. Review/reject drives before starting destructive surface testing.'])

    lines.extend([
        '',
        f"{'DEV':<7} {'SIZE':>6} {'STATUS':<18} {'STAGE':<18} {'CURRENT':>8} {'OVERALL':>8} {'ETA':>10}",
        '-' * 84,
    ])

    for drive in (state.get('drives') or {}).values():
        dev = Path(drive.get('dev', '?')).name
        status = str(drive.get('workflow_status') or drive.get('result') or drive.get('status') or drive.get('precheck') or 'WAITING').upper()
        stage = str(drive.get('stage') or 'waiting').replace('-', ' ').title()
        current = float(drive.get('stage_progress') or 0) * 100
        overall = float(drive.get('overall_progress') or overall_for_drive(drive)) * 100
        eta = format_duration(drive.get('stage_eta_seconds'))
        lines.append(
            f"{dev:<7} {_size_tb(drive):>5.1f}T {status:<18.18} {stage:<18.18} "
            f"{current:>7.1f}% {overall:>7.1f}% {eta:>10}"
        )

    return '\n'.join(lines)


def main():
    print(render_status(load_state(STATE)))


if __name__ == '__main__':
    main()
