# station.py
import json
import os
from pathlib import Path

from .progress import atomic_write_json

BASE = Path(os.environ.get('DISKQUAL_HOME', '/opt/diskqual'))
LEGACY_STATE = BASE / 'state.json'
LEGACY_REGISTRY = BASE / 'workflow.json'
JOBS = BASE / 'jobs'
DRIVE_WORKFLOW = BASE / 'workflow-drives'
SMART_OBSERVED = BASE / 'operator' / 'smart-observed.json'
RUNNING_BATCH_STATES = {'RUNNING', 'SMART_LONG_RUNNING', 'SURFACE_RUNNING'}


def _load_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError, TypeError):
        return default


def load_legacy_registry():
    data = _load_json(LEGACY_REGISTRY, {})
    return data if isinstance(data, dict) else {}


def load_drive_workflow(serial):
    serial = str(serial or '')
    if not serial:
        return {}
    data = _load_json(DRIVE_WORKFLOW / f'{serial}.json', None)
    if isinstance(data, dict):
        return data
    legacy = load_legacy_registry().get(serial, {})
    return legacy if isinstance(legacy, dict) else {}


def save_drive_workflow(serial, data):
    serial = str(serial or '')
    if not serial:
        raise ValueError('Drive serial is required for workflow state.')
    DRIVE_WORKFLOW.mkdir(parents=True, exist_ok=True)
    atomic_write_json(DRIVE_WORKFLOW / f'{serial}.json', data)


def load_smart_observed():
    data = _load_json(SMART_OBSERVED, {})
    drives = data.get('drives', {}) if isinstance(data, dict) else {}
    return drives if isinstance(drives, dict) else {}


def job_state_paths(include_legacy=True):
    paths = []
    if include_legacy and LEGACY_STATE.exists():
        paths.append(LEGACY_STATE)
    if JOBS.exists():
        paths.extend(sorted(JOBS.glob('*.json')))
    return paths


def load_job_states(include_legacy=True):
    states = []
    for path in job_state_paths(include_legacy=include_legacy):
        state = _load_json(path, None)
        if not isinstance(state, dict):
            continue
        state = dict(state)
        state['_state_path'] = str(path)
        states.append(state)
    return states


def _state_updated(state):
    return str(state.get('updated_utc') or state.get('started_utc') or '')


def drive_activity_map():
    activity = {}
    for state in load_job_states():
        batch_status = str(state.get('status') or '').upper()
        for drive in (state.get('drives') or {}).values():
            if not isinstance(drive, dict):
                continue
            serial = str(drive.get('serial') or drive.get('id') or '')
            if not serial:
                continue
            row = dict(drive)
            row['_batch_status'] = batch_status
            row['_batch_id'] = state.get('batch_id')
            row['_job_id'] = state.get('job_id') or state.get('batch_id')
            row['_state_updated_utc'] = _state_updated(state)
            existing = activity.get(serial)
            running = str(row.get('status') or '').upper() == 'RUNNING' and batch_status in RUNNING_BATCH_STATES
            existing_running = bool(existing) and str(existing.get('status') or '').upper() == 'RUNNING' and str(existing.get('_batch_status') or '').upper() in RUNNING_BATCH_STATES
            if existing is None or (running and not existing_running) or (running == existing_running and row['_state_updated_utc'] >= existing.get('_state_updated_utc', '')):
                activity[serial] = row
    return activity


def active_serials():
    active = set()
    for serial, row in drive_activity_map().items():
        if str(row.get('status') or '').upper() == 'RUNNING' and str(row.get('_batch_status') or '').upper() in RUNNING_BATCH_STATES:
            active.add(serial)
    for serial, row in load_smart_observed().items():
        if isinstance(row, dict) and row.get('smart_long_running'):
            active.add(serial)
    return active


def station_rows(inventory):
    activity = drive_activity_map()
    observed = load_smart_observed()
    rows = []
    for drive in inventory or []:
        row = dict(drive)
        serial = str(row.get('serial') or row.get('id') or '')
        workflow = load_drive_workflow(serial)
        if workflow:
            row['workflow_status'] = workflow.get('status')
            row['smart_long_result'] = workflow.get('smart_long_result')
            row['smart_long_detail'] = workflow.get('smart_long_detail')
            row['surface_result'] = workflow.get('surface_result')
            row['surface_detail'] = workflow.get('surface_detail')
        current = activity.get(serial)
        if current:
            for key in (
                'status', 'result', 'stage', 'stage_progress', 'overall_progress',
                'stage_started_utc', 'stage_elapsed_seconds', 'stage_eta_seconds',
                'throughput_mib_s', 'message', 'error', 'workflow_status',
            ):
                if key in current:
                    row[key] = current[key]
            row['active_batch_id'] = current.get('_batch_id')
            row['active_job_id'] = current.get('_job_id')
            row['batch_status'] = current.get('_batch_status')

        live = observed.get(serial, {})
        if isinstance(live, dict) and live.get('smart_long_running'):
            row['status'] = 'RUNNING'
            row['stage'] = 'smart-long'
            row['stage_progress'] = live.get('stage_progress', row.get('stage_progress', 0))
            row['stage_eta_seconds'] = live.get('stage_eta_seconds', row.get('stage_eta_seconds'))
            row['message'] = live.get('message', row.get('message', 'SMART extended self-test running'))
            row['firmware_observed'] = True
        rows.append(row)
    return rows
