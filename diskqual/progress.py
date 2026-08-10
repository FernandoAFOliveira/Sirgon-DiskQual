# progress.py
import json
import os
from datetime import datetime, timezone
from pathlib import Path

STAGE_ORDER = [
    'baseline-smart',
    'smart-short',
    'smart-long',
    'surface-write',
    'surface-verify',
    'final-smart',
    'classify',
]

STAGE_WEIGHTS = {
    'baseline-smart': 0.02,
    'smart-short': 0.03,
    'smart-long': 0.15,
    'surface-write': 0.38,
    'surface-verify': 0.38,
    'final-smart': 0.02,
    'classify': 0.02,
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


def load_state(path):
    path = Path(path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def make_drive_state(drive):
    return {
        'id': drive['id'],
        'dev': drive['dev'],
        'serial': drive['serial'],
        'model': drive['model'],
        'size_bytes': int(drive.get('size_bytes') or 0),
        'protocol': drive.get('protocol', 'UNKNOWN'),
        'precheck': drive.get('precheck', 'UNKNOWN'),
        'precheck_reason': drive.get('precheck_reason', ''),
        'status': 'WAITING',
        'stage': 'baseline-smart',
        'stage_progress': 0.0,
        'overall_progress': 0.0,
        'stage_started_utc': None,
        'stage_elapsed_seconds': 0,
        'stage_eta_seconds': None,
        'throughput_mib_s': None,
        'message': 'Waiting to start',
        'completed_stages': [],
        'result': None,
        'error': None,
    }


def create_batch_state(batch_id, drives):
    return {
        'version': 2,
        'batch_id': batch_id,
        'status': 'RUNNING',
        'started_utc': utc_now(),
        'updated_utc': utc_now(),
        'ended_utc': None,
        'drives': {d['id']: make_drive_state(d) for d in drives},
    }


def overall_for_drive(drive_state):
    if drive_state.get('status') == 'REJECTED':
        return 1.0
    completed = sum(STAGE_WEIGHTS.get(stage, 0.0) for stage in drive_state.get('completed_stages', []))
    current_stage = drive_state.get('stage')
    current = STAGE_WEIGHTS.get(current_stage, 0.0) * max(0.0, min(1.0, drive_state.get('stage_progress', 0.0)))
    return min(1.0, completed + current)


def weighted_batch_progress(state):
    drives = list(state.get('drives', {}).values())
    if not drives:
        return 0.0
    weights = [max(1, int(d.get('size_bytes') or 0)) for d in drives]
    total = sum(weights)
    return sum(overall_for_drive(d) * w for d, w in zip(drives, weights)) / total


def update_drive(state, drive_id, **changes):
    d = state['drives'][drive_id]
    d.update(changes)
    if d.get('stage_started_utc'):
        try:
            start = datetime.fromisoformat(d['stage_started_utc'])
            d['stage_elapsed_seconds'] = max(0, int((datetime.now(timezone.utc) - start).total_seconds()))
        except ValueError:
            pass
    d['overall_progress'] = overall_for_drive(d)
    state['updated_utc'] = utc_now()


def reject_drive(state, drive_id, reason):
    d = state['drives'][drive_id]
    d['status'] = 'REJECTED'
    d['result'] = 'REJECT'
    d['stage'] = 'precheck'
    d['stage_progress'] = 1.0
    d['overall_progress'] = 1.0
    d['stage_eta_seconds'] = 0
    d['message'] = reason
    d['precheck'] = 'REJECT'
    d['precheck_reason'] = reason
    state['updated_utc'] = utc_now()


def begin_stage(state, drive_id, stage, message=''):
    d = state['drives'][drive_id]
    d['stage'] = stage
    d['stage_progress'] = 0.0
    d['stage_started_utc'] = utc_now()
    d['stage_elapsed_seconds'] = 0
    d['stage_eta_seconds'] = None
    d['throughput_mib_s'] = None
    d['status'] = 'RUNNING'
    d['message'] = message or stage
    d['error'] = None
    d['overall_progress'] = overall_for_drive(d)
    state['updated_utc'] = utc_now()


def complete_stage(state, drive_id, stage, message='Completed'):
    d = state['drives'][drive_id]
    if stage not in d['completed_stages']:
        d['completed_stages'].append(stage)
    d['stage'] = stage
    d['stage_progress'] = 1.0
    d['stage_eta_seconds'] = 0
    d['message'] = message
    d['overall_progress'] = overall_for_drive(d)
    state['updated_utc'] = utc_now()


def fail_drive(state, drive_id, message):
    d = state['drives'][drive_id]
    d['status'] = 'FAILED'
    d['error'] = message
    d['message'] = message
    state['updated_utc'] = utc_now()


def finish_drive(state, drive_id, result, message='Qualification complete'):
    d = state['drives'][drive_id]
    d['status'] = 'COMPLETE' if result == 'PASS' else result
    d['result'] = result
    d['stage_progress'] = 1.0
    d['overall_progress'] = 1.0
    d['stage_eta_seconds'] = 0
    d['message'] = message
    state['updated_utc'] = utc_now()


def format_duration(seconds):
    if seconds is None:
        return 'calculating...'
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f'{h}h {m:02d}m'
    if m:
        return f'{m}m {s:02d}s'
    return f'{s}s'


def bar(progress, width=34):
    progress = max(0.0, min(1.0, progress or 0.0))
    filled = int(round(progress * width))
    return '[' + '=' * filled + '-' * (width - filled) + ']'


def _size_text(size_bytes):
    value = float(size_bytes or 0)
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if value < 1000 or unit == 'TB':
            return f'{value:.1f} {unit}' if unit in ('GB', 'TB') else f'{value:.0f} {unit}'
        value /= 1000
    return f'{value:.1f} TB'


def render_dashboard(state):
    if not state:
        return 'DiskQual\nNo active batch. Run: diskqual qualify --yes'

    lines = []
    batch_progress = weighted_batch_progress(state)
    drives = list(state.get('drives', {}).values())
    completed = sum(1 for d in drives if d.get('status') in ('COMPLETE', 'PASS', 'REVIEW'))
    rejected = sum(1 for d in drives if d.get('status') == 'REJECTED')
    failed = sum(1 for d in drives if d.get('status') in ('FAILED', 'BAD'))
    running = sum(1 for d in drives if d.get('status') == 'RUNNING')
    waiting = max(0, len(drives) - completed - rejected - failed - running)

    lines.append('DISKQUAL - Disk Qualification Station')
    lines.append(f"Batch: {state.get('batch_id', 'unknown')}   Status: {state.get('status', 'UNKNOWN')}")
    lines.append('')
    lines.append('TOTAL BATCH')
    lines.append(f"{bar(batch_progress, 50)} {batch_progress * 100:5.1f}%")
    lines.append(f'{completed} completed | {running} testing | {waiting} waiting | {rejected} rejected | {failed} failed')
    lines.append('')

    for d in drives:
        current = float(d.get('stage_progress') or 0.0)
        overall = float(d.get('overall_progress') or overall_for_drive(d))
        eta = format_duration(d.get('stage_eta_seconds'))
        elapsed = format_duration(d.get('stage_elapsed_seconds'))
        throughput = d.get('throughput_mib_s')
        rate = f' | {throughput:.1f} MiB/s' if isinstance(throughput, (int, float)) else ''
        result = d.get('result') or d.get('status') or 'WAITING'
        precheck = d.get('precheck', 'UNKNOWN')
        reason = d.get('precheck_reason', '')
        lines.append(f"{Path(d.get('dev', '?')).name:<6} {d.get('serial', '?'):<22} {_size_text(d.get('size_bytes')):<9} {result} | PRECHECK {precheck}")
        if reason:
            lines.append(f"       Precheck: {reason}")
        lines.append(f"       {d.get('stage', 'waiting')}: {d.get('message', '')}")
        lines.append(f"       Current {bar(current)} {current * 100:5.1f}% | elapsed {elapsed} | ETA {eta}{rate}")
        lines.append(f"       Overall {bar(overall)} {overall * 100:5.1f}%")
        lines.append('')

    lines.append('Refreshes automatically. Ctrl-C exits monitor only; tests continue.')
    return '\n'.join(lines)
