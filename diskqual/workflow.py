# workflow.py
import argparse
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .cli import discover, parse_attrs, parse_field, selftest_line, selftest_status, smart_text
from .engine import run_surface_test
from .precheck import classify_precheck
from .progress import atomic_write_json, begin_stage, complete_stage, create_batch_state, fail_drive, finish_drive, reject_drive, update_drive

BASE = Path(os.environ.get('DISKQUAL_HOME', '/opt/diskqual'))
STATE = BASE / 'state.json'
REGISTRY = BASE / 'workflow.json'
SELECTION = BASE / 'operator' / 'selection.json'
REPORTS = BASE / 'reports'


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _load_json(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def load_registry():
    data = _load_json(REGISTRY, {})
    return data if isinstance(data, dict) else {}


def save_registry(registry):
    atomic_write_json(REGISTRY, registry)


def selected_serials():
    data = _load_json(SELECTION, {})
    serials = data.get('serials', []) if isinstance(data, dict) else []
    return [str(serial) for serial in serials if serial]


def selected_drives():
    wanted = set(selected_serials())
    if not wanted:
        raise RuntimeError('No drives are selected.')
    drives = discover()
    found = [drive for drive in drives if drive.get('serial') in wanted]
    missing = wanted - {drive.get('serial') for drive in found}
    if missing:
        raise RuntimeError('Selected drive(s) are no longer present: ' + ', '.join(sorted(missing)))
    return found


def _save_state(state, lock):
    state['updated_utc'] = _utc_now()
    with lock:
        atomic_write_json(STATE, state)


def _long_test_passed(text):
    line = selftest_line(text)
    lower = line.lower()
    if not line:
        return False, 'SMART long test completed, but no final self-test result was found.'
    failure_words = ('fail', 'error', 'abort', 'interrupt', 'unknown', 'read failure', 'write failure')
    if any(word in lower for word in failure_words):
        return False, line
    if 'completed without error' in lower or 'completed successfully' in lower:
        return True, line
    if re.search(r'\bcompleted\b', lower):
        return True, line
    return False, line


def _smart_long_drive(drive, state, lock, registry, registry_lock, batch_dir, poll):
    serial = drive['serial']
    try:
        begin_stage(state, serial, 'smart-long', 'SMART extended self-test')
        _save_state(state, lock)
        output = smart_text(drive['dev'], ['-t', 'long'])
        match = re.search(r'Please wait\s+(\d+)\s+minutes', output, re.I)
        estimate = int(match.group(1)) * 60 if match else None
        start = time.monotonic()

        while True:
            time.sleep(poll)
            text = smart_text(drive['dev'], ['-a'])
            status = selftest_status(text)
            elapsed = time.monotonic() - start
            progress = min(0.99, elapsed / estimate) if estimate else 0.0
            eta = max(0, int(estimate - elapsed)) if estimate else None
            update_drive(state, serial, stage_progress=progress, stage_eta_seconds=eta, message=status or 'SMART extended self-test running')
            _save_state(state, lock)
            lower = (status or '').lower()
            if status and not any(token in lower for token in ('remaining', 'progress', 'self-test routine in progress')):
                break
            if selftest_line(text) and elapsed > 30 and not status:
                break

        final = smart_text(drive['dev'], ['-a'])
        (batch_dir / f'{serial}.smart-long.txt').write_text(smart_text(drive['dev'], ['-x']))
        passed, selftest = _long_test_passed(final)
        attrs = parse_attrs(final)
        health = parse_field(final, ['SMART overall-health self-assessment test result', 'SMART Health Status']) or 'UNKNOWN'
        precheck, precheck_reason = classify_precheck({**drive, **attrs, 'health': health})
        if precheck == 'REJECT':
            passed = False
            reason = f'{selftest}; {precheck_reason}' if selftest else precheck_reason
        else:
            reason = selftest

        complete_stage(state, serial, 'smart-long', 'SMART extended self-test complete')
        if passed:
            update_drive(state, serial, status='READY_FOR_SURFACE', result='SMART_LONG_PASSED', stage='smart-review', stage_progress=1.0, stage_eta_seconds=None, message=reason)
            workflow_status = 'READY_FOR_SURFACE'
        else:
            update_drive(state, serial, status='REJECTED', result='SMART_LONG_FAILED', stage='smart-review', stage_progress=1.0, stage_eta_seconds=None, message=reason)
            workflow_status = 'REJECTED'
        _save_state(state, lock)

        with registry_lock:
            registry[serial] = {
                'serial': serial,
                'dev': drive['dev'],
                'model': drive.get('model', ''),
                'size_bytes': drive.get('size_bytes', 0),
                'status': workflow_status,
                'smart_long_result': 'PASS' if passed else 'FAIL',
                'smart_long_detail': reason,
                'smart_long_utc': _utc_now(),
            }
            save_registry(registry)
    except Exception as exc:
        fail_drive(state, serial, str(exc))
        _save_state(state, lock)
        with registry_lock:
            registry[serial] = {
                'serial': serial,
                'dev': drive.get('dev', ''),
                'model': drive.get('model', ''),
                'size_bytes': drive.get('size_bytes', 0),
                'status': 'REJECTED',
                'smart_long_result': 'FAIL',
                'smart_long_detail': str(exc),
                'smart_long_utc': _utc_now(),
            }
            save_registry(registry)


def run_smart_long(poll=10):
    drives = selected_drives()
    batch_id = 'smart-long_' + datetime.now(timezone.utc).strftime('%Y-%m-%d_%H-%M-%S')
    batch_dir = REPORTS / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)
    state = create_batch_state(batch_id, drives)
    state['status'] = 'SMART_LONG_RUNNING'
    state['workflow_phase'] = 'smart-long'
    lock = threading.Lock()
    registry = load_registry()
    registry_lock = threading.Lock()

    accepted = []
    for drive in drives:
        if drive.get('precheck') == 'REJECT':
            reject_drive(state, drive['id'], drive.get('precheck_reason', 'Inventory precheck failed'))
            registry[drive['serial']] = {
                'serial': drive['serial'], 'dev': drive['dev'], 'model': drive.get('model', ''),
                'size_bytes': drive.get('size_bytes', 0), 'status': 'REJECTED',
                'smart_long_result': 'NOT_RUN', 'smart_long_detail': drive.get('precheck_reason', ''), 'smart_long_utc': _utc_now(),
            }
        else:
            accepted.append(drive)
    save_registry(registry)
    _save_state(state, lock)

    threads = [threading.Thread(target=_smart_long_drive, args=(drive, state, lock, registry, registry_lock, batch_dir, poll), name=drive['serial']) for drive in accepted]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    state['status'] = 'SMART_REVIEW'
    state['workflow_phase'] = 'smart-review'
    state['ended_utc'] = _utc_now()
    _save_state(state, lock)


def _surface_drive(drive, state, lock, registry, registry_lock, batch_dir, poll):
    serial = drive['serial']
    try:
        run_surface_test(drive, state, lock, batch_dir / f"{Path(drive['dev']).name}.surface.log", poll)
        begin_stage(state, serial, 'final-smart', 'Capturing final SMART')
        _save_state(state, lock)
        final = smart_text(drive['dev'], ['-x'])
        (batch_dir / f'{serial}.after.smart.txt').write_text(final)
        attrs = parse_attrs(final)
        health = parse_field(final, ['SMART overall-health self-assessment test result', 'SMART Health Status']) or 'UNKNOWN'
        decision, reason = classify_precheck({**drive, **attrs, 'health': health})
        complete_stage(state, serial, 'final-smart', 'Final SMART capture complete')
        if decision == 'REJECT':
            finish_drive(state, serial, 'BAD', reason)
            workflow_status = 'REJECTED'
        else:
            finish_drive(state, serial, decision, reason)
            workflow_status = 'QUALIFIED' if decision == 'PASS' else 'REVIEW'
        _save_state(state, lock)
        with registry_lock:
            current = dict(registry.get(serial, {}))
            current.update({'status': workflow_status, 'surface_result': decision, 'surface_detail': reason, 'surface_utc': _utc_now()})
            registry[serial] = current
            save_registry(registry)
    except Exception as exc:
        fail_drive(state, serial, str(exc))
        _save_state(state, lock)
        with registry_lock:
            current = dict(registry.get(serial, {}))
            current.update({'status': 'REJECTED', 'surface_result': 'FAIL', 'surface_detail': str(exc), 'surface_utc': _utc_now()})
            registry[serial] = current
            save_registry(registry)


def run_surface(poll=10):
    drives = selected_drives()
    registry = load_registry()
    not_ready = [drive['serial'] for drive in drives if registry.get(drive['serial'], {}).get('status') != 'READY_FOR_SURFACE']
    if not_ready:
        raise RuntimeError('Surface test refused. These drives have not passed SMART Long: ' + ', '.join(not_ready))

    batch_id = 'surface_' + datetime.now(timezone.utc).strftime('%Y-%m-%d_%H-%M-%S')
    batch_dir = REPORTS / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)
    state = create_batch_state(batch_id, drives)
    state['status'] = 'SURFACE_RUNNING'
    state['workflow_phase'] = 'surface'
    lock = threading.Lock()
    registry_lock = threading.Lock()
    _save_state(state, lock)

    threads = [threading.Thread(target=_surface_drive, args=(drive, state, lock, registry, registry_lock, batch_dir, poll), name=drive['serial']) for drive in drives]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    state['status'] = 'COMPLETE'
    state['workflow_phase'] = 'complete'
    state['ended_utc'] = _utc_now()
    _save_state(state, lock)


def main():
    parser = argparse.ArgumentParser(prog='Sirgon DiskQual phased worker')
    parser.add_argument('phase', choices=('smart-long', 'surface'))
    parser.add_argument('--poll', type=int, default=10)
    args = parser.parse_args()
    if args.phase == 'smart-long':
        run_smart_long(args.poll)
    else:
        run_surface(args.poll)


if __name__ == '__main__':
    main()
