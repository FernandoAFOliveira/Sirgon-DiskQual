# progress.py
import json
import os
from datetime import datetime, timezone
from pathlib import Path

STAGE_ORDER = [
    'baseline-smart',
    'smart-short',
    'smart-long',
    'surface-test',
    'surface-write',
    'surface-verify',
    'final-smart',
    'classify',
]

STAGE_WEIGHTS = {
    'baseline-smart': 0.02,
    'smart-short': 0.03,
    'smart-long': 0.15,
    # Current engine: badblocks -w performs the destructive write and read/compare
    # in one combined stage. Keep the legacy split weights below for old states.
    'surface-test': 0.76,
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
        'version': 3,
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
    if d.get('status') in ('READY_FOR_SURFACE', 'REJECTED', 'QUALIFIED', 'REVIEW'):
        d['workflow_status'] = d['status']
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
    d['workflow_status'] = 'REJECTED'
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
    d['workflow_status'] = 'REJECTED'
    d['error'] = message
    d['message'] = message
    state['updated_utc'] = utc_now()


def finish_drive(state, drive_id, result, message='Qualification complete'):
    d = state['drives'][drive_id]
    d['status'] = 'COMPLETE' if result == 'PASS' else result
    d['workflow_status'] = 'QUALIFIED' if result == 'PASS' else ('REVIEW' if result == 'REVIEW' else 'REJECTED')
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
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if days:
        return f'{days}d {hours:02d}h'
    if hours:
        return f'{hours}h {minutes:02d}m'
    if minutes:
        return f'{minutes}m {seconds:02d}s'
    return f'{seconds}s'
