# status.py
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .progress import format_duration, overall_for_drive
from .station import load_job_states, station_rows

BASE = Path(os.environ.get('DISKQUAL_HOME', '/opt/diskqual'))
DRIVES = BASE / 'drives.json'
STALE_AFTER_SECONDS = 120
RUNNING_STATES = {'RUNNING', 'SMART_LONG_RUNNING', 'SURFACE_RUNNING'}


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
    result = _run(['systemctl', 'list-units', '--type=service', '--state=running', '--no-legend', 'diskqual-*.service'])
    if result.stdout.strip():
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


def _load_inventory():
    try:
        data = json.loads(DRIVES.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _size_tb(drive):
    return float(drive.get('size_bytes') or 0) / 1_000_000_000_000


def render_station_status():
    inventory = _load_inventory()
    rows = station_rows(inventory)
    jobs = load_job_states()
    running_jobs = [state for state in jobs if str(state.get('status') or '').upper() in RUNNING_STATES]

    lines = [
        'SIRGON DISKQUAL — STATION STATUS',
        f'Candidate drives: {len(rows)}',
        f'Active jobs: {len(running_jobs)}',
        '',
        f"{'DEV':<7} {'SIZE':>6} {'STATUS':<20} {'STAGE':<18} {'CURRENT':>8} {'OVERALL':>8} {'ETA':>10}",
        '-' * 88,
    ]

    for drive in rows:
        dev = Path(drive.get('dev', '?')).name
        workflow = str(drive.get('workflow_status') or '').upper()
        status = str(drive.get('status') or '').upper()
        if status == 'RUNNING':
            display = 'RUNNING'
        else:
            display = workflow or str(drive.get('result') or drive.get('precheck') or 'IDLE').upper()
        stage = str(drive.get('stage') or ('ready for surface' if workflow == 'READY_FOR_SURFACE' else 'idle')).replace('-', ' ').title()
        current = float(drive.get('stage_progress') or 0) * 100
        overall = float(drive.get('overall_progress') or overall_for_drive(drive)) * 100
        eta = format_duration(drive.get('stage_eta_seconds')) if status == 'RUNNING' else '—'
        lines.append(
            f"{dev:<7} {_size_tb(drive):>5.1f}T {display:<20.20} {stage:<18.18} "
            f"{current:>7.1f}% {overall:>7.1f}% {eta:>10}"
        )

    if running_jobs:
        lines.extend(['', 'ACTIVE JOBS'])
        for state in running_jobs:
            lines.append(f"  {state.get('job_id') or state.get('batch_id', 'unknown')}  {state.get('status', 'UNKNOWN')}")

    return '\n'.join(lines)


def render_status(state):
    # Compatibility entry point retained for callers that still pass one state.
    return render_station_status()


def main():
    print(render_station_status())


if __name__ == '__main__':
    main()
