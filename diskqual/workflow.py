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
from .identity import resolve_serial_device
from .keep_awake import KeepAwake
from .precheck import classify_precheck
from .progress import atomic_write_json, begin_stage, complete_stage, create_batch_state, fail_drive, finish_drive, reject_drive, update_drive
from .qualification_policy import classify_qualification
from .station import active_serials, load_drive_workflow, save_drive_workflow

BASE = Path(os.environ.get('DISKQUAL_HOME', '/opt/diskqual'))
DEFAULT_STATE = BASE / 'state.json'
DEFAULT_SELECTION = BASE / 'operator' / 'selection.json'
REPORTS = BASE / 'reports'


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _load_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return default


def selected_serials(selection_path):
    data = _load_json(selection_path, {})
    serials = data.get('serials', []) if isinstance(data, dict) else []
    return [str(serial) for serial in serials if serial]


def selected_drives(selection_path):
    wanted = set(selected_serials(selection_path))
    if not wanted:
        raise RuntimeError('No drives are selected.')
    drives = discover()
    found = [drive for drive in drives if drive.get('serial') in wanted]
    missing = wanted - {drive.get('serial') for drive in found}
    if missing:
        raise RuntimeError('Selected drive(s) are no longer present: ' + ', '.join(sorted(missing)))
    return found


def _save_state(state, lock, state_path):
    state['updated_utc'] = _utc_now()
    with lock:
        atomic_write_json(state_path, state)


def _smart_record(drive, text):
    attrs = parse_attrs(text)
    health = parse_field(text, ['SMART overall-health self-assessment test result', 'SMART Health Status']) or 'UNKNOWN'
    return {**drive, **attrs, 'health': health}


def _execution_code(text):
    match = re.search(r'Self-test execution status:\s+\(\s*(\d+)\)', text)
    return int(match.group(1)) if match else None


def _selftest_is_running(text, status=None):
    """Recognize both ATA execution-status text and SAS self-test log rows."""
    status_lower = (status or selftest_status(text) or '').lower()
    if any(token in status_lower for token in ('remaining', 'progress', 'self-test routine in progress')):
        return True
    line = selftest_line(text)
    line_lower = line.lower()
    return bool(line and ('in progress' in line_lower or re.search(r'\bnow\b', line_lower)))


def _long_test_passed(text, observed_running=False):
    line = selftest_line(text)
    lower = line.lower()
    if line:
        if 'in progress' in lower or re.search(r'\bnow\b', lower):
            return False, line + '; SMART long self-test is still running'
        if 'completed without error' in lower or 'completed successfully' in lower:
            return True, line
        failure_words = ('fail', 'error', 'abort', 'interrupt', 'unknown', 'read failure', 'write failure')
        if any(word in lower for word in failure_words):
            return False, line
        if re.search(r'\bcompleted\b', lower):
            return True, line
        return False, line

    code = _execution_code(text)
    status = selftest_status(text)
    if observed_running and code == 0:
        detail = status or 'SMART execution status returned to idle after DiskQual observed the extended self-test running'
        return True, detail + '; self-test log entry unavailable'
    return False, 'SMART long test stopped, but no verifiable completion result was found.'


def _resolved_smart(serial, args):
    dev = resolve_serial_device(serial)
    return dev, smart_text(dev, args)


def _smart_long_drive(drive, state, lock, batch_dir, poll, state_path):
    serial = drive['serial']
    try:
        begin_stage(state, serial, 'smart-long', 'SMART extended self-test')
        dev, output = _resolved_smart(serial, ['-t', 'long'])
        update_drive(state, serial, dev=dev)
        _save_state(state, lock, state_path)
        match = re.search(r'Please wait\s+(\d+)\s+minutes', output, re.I)
        estimate = int(match.group(1)) * 60 if match else None
        start = time.monotonic()
        observed_running = False
        missing_polls = 0

        while True:
            time.sleep(poll)
            try:
                dev, text = _resolved_smart(serial, ['-a'])
                missing_polls = 0
            except RuntimeError as exc:
                missing_polls += 1
                elapsed = time.monotonic() - start
                progress = min(0.99, elapsed / estimate) if estimate else 0.0
                update_drive(
                    state, serial,
                    stage_progress=progress,
                    stage_eta_seconds=max(0, int(estimate - elapsed)) if estimate else None,
                    message=f'Drive identity temporarily unavailable ({missing_polls}/6): {exc}',
                )
                _save_state(state, lock, state_path)
                if missing_polls >= 6:
                    raise
                continue

            status = selftest_status(text)
            code = _execution_code(text)
            test_line = selftest_line(text)
            elapsed = time.monotonic() - start
            progress = min(0.99, elapsed / estimate) if estimate else 0.0
            eta = max(0, int(estimate - elapsed)) if estimate else None
            display_message = status or test_line or 'SMART extended self-test running'
            update_drive(state, serial, dev=dev, stage_progress=progress, stage_eta_seconds=eta, message=display_message)
            _save_state(state, lock, state_path)

            if _selftest_is_running(text, status=status) or (code is not None and code != 0):
                observed_running = True
                continue
            if observed_running and code == 0:
                break
            if status:
                break
            # SAS disks often expose progress only through the self-test log. A
            # non-running log row after we have observed the test running is a
            # completion/failure record and may now be evaluated.
            if test_line and observed_running:
                break
            if test_line and elapsed > 30:
                # A completed/failed SAS log row may exist immediately after a
                # very short or previously-started test. Never treat an explicit
                # "in progress" row as terminal.
                break

        dev, final = _resolved_smart(serial, ['-a'])
        _, extended = _resolved_smart(serial, ['-x'])
        (batch_dir / f'{serial}.smart-long.txt').write_text(extended)
        passed, selftest = _long_test_passed(final, observed_running=observed_running)
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
            update_drive(state, serial, dev=dev, status='READY_FOR_SURFACE', workflow_status='READY_FOR_SURFACE', result='SMART_LONG_PASSED', stage='smart-review', stage_progress=1.0, stage_eta_seconds=None, message=reason)
            workflow_status = 'READY_FOR_SURFACE'
        else:
            update_drive(state, serial, dev=dev, status='REJECTED', workflow_status='REJECTED', result='SMART_LONG_FAILED', stage='smart-review', stage_progress=1.0, stage_eta_seconds=None, message=reason)
            workflow_status = 'REJECTED'
        _save_state(state, lock, state_path)
        save_drive_workflow(serial, {
            'serial': serial,
            'dev': dev,
            'model': drive.get('model', ''),
            'size_bytes': drive.get('size_bytes', 0),
            'status': workflow_status,
            'smart_long_result': 'PASS' if passed else 'FAIL',
            'smart_long_detail': reason,
            'smart_long_utc': _utc_now(),
        })
    except Exception as exc:
        fail_drive(state, serial, str(exc))
        update_drive(state, serial, workflow_status='REJECTED')
        _save_state(state, lock, state_path)
        current_dev = ''
        try:
            current_dev = resolve_serial_device(serial)
        except RuntimeError:
            pass
        save_drive_workflow(serial, {
            'serial': serial,
            'dev': current_dev or drive.get('dev', ''),
            'model': drive.get('model', ''),
            'size_bytes': drive.get('size_bytes', 0),
            'status': 'REJECTED',
            'smart_long_result': 'FAIL',
            'smart_long_detail': str(exc),
            'smart_long_utc': _utc_now(),
        })


def run_smart_long(selection_path, state_path, job_id, poll=10):
    drives = selected_drives(selection_path)
    already_active = active_serials() & {drive['serial'] for drive in drives}
    if already_active:
        raise RuntimeError('Selected drive(s) already have a running DiskQual test: ' + ', '.join(sorted(already_active)))

    batch_id = job_id or ('smart-long_' + datetime.now(timezone.utc).strftime('%Y-%m-%d_%H-%M-%S'))
    batch_dir = REPORTS / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)
    state = create_batch_state(batch_id, drives)
    state['job_id'] = batch_id
    state['status'] = 'SMART_LONG_RUNNING'
    state['workflow_phase'] = 'smart-long'
    lock = threading.Lock()

    accepted = []
    for drive in drives:
        if drive.get('precheck') == 'REJECT':
            reject_drive(state, drive['id'], drive.get('precheck_reason', 'Inventory precheck failed'))
            save_drive_workflow(drive['serial'], {
                'serial': drive['serial'], 'dev': drive['dev'], 'model': drive.get('model', ''),
                'size_bytes': drive.get('size_bytes', 0), 'status': 'REJECTED',
                'smart_long_result': 'NOT_RUN', 'smart_long_detail': drive.get('precheck_reason', ''), 'smart_long_utc': _utc_now(),
            })
        else:
            accepted.append(drive)
    _save_state(state, lock, state_path)

    threads = [threading.Thread(target=_smart_long_drive, args=(drive, state, lock, batch_dir, poll, state_path), name=drive['serial']) for drive in accepted]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    state['status'] = 'SMART_REVIEW'
    state['workflow_phase'] = 'smart-review'
    state['ended_utc'] = _utc_now()
    _save_state(state, lock, state_path)


def _surface_drive(drive, state, lock, batch_dir, poll, state_path):
    serial = drive['serial']
    try:
        dev = resolve_serial_device(serial)
        update_drive(state, serial, dev=dev)
        baseline_text = smart_text(dev, ['-x'])
        (batch_dir / f'{serial}.surface-before.smart.txt').write_text(baseline_text)
        baseline = _smart_record({**drive, 'dev': dev}, baseline_text)

        surface = run_surface_test({**drive, 'dev': dev}, state, lock, batch_dir / f'{serial}.surface.log', poll)

        begin_stage(state, serial, 'final-smart', 'Capturing final SMART')
        _save_state(state, lock, state_path)
        final_dev = resolve_serial_device(serial)
        update_drive(state, serial, dev=final_dev)
        final_text = smart_text(final_dev, ['-x'])
        (batch_dir / f'{serial}.after.smart.txt').write_text(final_text)
        final = _smart_record({**drive, 'dev': final_dev}, final_text)
        complete_stage(state, serial, 'final-smart', 'Final SMART capture complete')

        decision, reason = classify_qualification(baseline, final, surface)
        result = 'BAD' if decision == 'REJECTED' else decision
        finish_drive(state, serial, result, reason)
        update_drive(state, serial, dev=final_dev, workflow_status=decision, surface_result=surface)
        _save_state(state, lock, state_path)

        current = dict(load_drive_workflow(serial))
        current.update({
            'serial': serial,
            'dev': final_dev,
            'model': drive.get('model', ''),
            'size_bytes': drive.get('size_bytes', 0),
            'status': decision,
            'surface_result': decision,
            'surface_detail': reason,
            'surface_metrics': surface,
            'surface_utc': _utc_now(),
        })
        save_drive_workflow(serial, current)
    except Exception as exc:
        fail_drive(state, serial, str(exc))
        update_drive(state, serial, workflow_status='REJECTED')
        _save_state(state, lock, state_path)
        current = dict(load_drive_workflow(serial))
        current.update({'serial': serial, 'dev': drive.get('dev', ''), 'model': drive.get('model', ''), 'size_bytes': drive.get('size_bytes', 0), 'status': 'REJECTED', 'surface_result': 'FAIL', 'surface_detail': str(exc), 'surface_utc': _utc_now()})
        save_drive_workflow(serial, current)


def run_surface(selection_path, state_path, job_id, poll=10):
    drives = selected_drives(selection_path)
    already_active = active_serials() & {drive['serial'] for drive in drives}
    if already_active:
        raise RuntimeError('Selected drive(s) already have a running DiskQual test: ' + ', '.join(sorted(already_active)))
    not_ready = [drive['serial'] for drive in drives if load_drive_workflow(drive['serial']).get('status') != 'READY_FOR_SURFACE']
    if not_ready:
        raise RuntimeError('Surface test refused. These drives have not passed SMART Long: ' + ', '.join(not_ready))

    batch_id = job_id or ('surface_' + datetime.now(timezone.utc).strftime('%Y-%m-%d_%H-%M-%S'))
    batch_dir = REPORTS / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)
    state = create_batch_state(batch_id, drives)
    state['job_id'] = batch_id
    state['status'] = 'SURFACE_RUNNING'
    state['workflow_phase'] = 'surface'
    lock = threading.Lock()
    _save_state(state, lock, state_path)

    threads = [threading.Thread(target=_surface_drive, args=(drive, state, lock, batch_dir, poll, state_path), name=drive['serial']) for drive in drives]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    state['status'] = 'COMPLETE'
    state['workflow_phase'] = 'complete'
    state['ended_utc'] = _utc_now()
    _save_state(state, lock, state_path)


def main():
    parser = argparse.ArgumentParser(prog='Sirgon DiskQual phased worker')
    parser.add_argument('phase', choices=('smart-long', 'surface'))
    parser.add_argument('--poll', type=int, default=10)
    parser.add_argument('--selection-path', default=str(DEFAULT_SELECTION))
    parser.add_argument('--state-path', default=str(DEFAULT_STATE))
    parser.add_argument('--job-id', default='')
    args = parser.parse_args()
    selection_path = Path(args.selection_path)
    state_path = Path(args.state_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    with KeepAwake.testing_only():
        if args.phase == 'smart-long':
            run_smart_long(selection_path, state_path, args.job_id, args.poll)
        else:
            run_surface(selection_path, state_path, args.job_id, args.poll)


if __name__ == '__main__':
    main()
