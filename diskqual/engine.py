# engine.py
import argparse
import csv
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .cli import discover, parse_attrs, parse_field, selftest_line, selftest_status, smart_text
from .progress import atomic_write_json, begin_stage, complete_stage, create_batch_state, finish_drive, reject_drive, update_drive
from .qualification_policy import classify_qualification
from .surface import run_adaptive_surface_test

BASE = Path(os.environ.get('DISKQUAL_HOME', '/opt/diskqual'))
REPORTS = BASE / 'reports'
STATE = Path(os.environ.get('DISKQUAL_STATE', str(BASE / 'state.json')))


def save_state(state, lock):
    with lock:
        atomic_write_json(STATE, state)


def wait_smart_long(drive, state, lock, poll):
    begin_stage(state, drive['id'], 'smart-long', 'SMART extended self-test')
    save_state(state, lock)
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
        update_drive(state, drive['id'], stage_progress=progress, stage_eta_seconds=eta, message=status or 'SMART extended self-test running')
        save_state(state, lock)
        lower = (status or '').lower()
        if status and not any(x in lower for x in ('remaining', 'progress', 'self-test routine in progress')):
            break
        if selftest_line(text) and elapsed > 30 and not status:
            break

    complete_stage(state, drive['id'], 'smart-long', 'SMART extended self-test complete')
    save_state(state, lock)


def run_surface_test(drive, state, lock, log_path, poll):
    """Compatibility entry point used by phased and legacy qualification workers."""
    return run_adaptive_surface_test(drive, state, lock, log_path, poll, save_state)


def _smart_record(drive, text):
    attrs = parse_attrs(text)
    health = parse_field(text, ['SMART overall-health self-assessment test result', 'SMART Health Status']) or 'UNKNOWN'
    return {**drive, **attrs, 'health': health}


def qualify_drive(drive, state, lock, batch_dir, poll):
    try:
        begin_stage(state, drive['id'], 'baseline-smart', 'Capturing baseline SMART')
        save_state(state, lock)
        baseline_text = smart_text(drive['dev'], ['-x'])
        (batch_dir / f"{drive['serial']}.before.smart.txt").write_text(baseline_text)
        baseline = _smart_record(drive, baseline_text)
        complete_stage(state, drive['id'], 'baseline-smart')
        save_state(state, lock)

        begin_stage(state, drive['id'], 'smart-short', 'SMART short self-test')
        save_state(state, lock)
        smart_text(drive['dev'], ['-t', 'short'])
        time.sleep(90)
        complete_stage(state, drive['id'], 'smart-short', 'SMART short test interval complete')
        save_state(state, lock)

        wait_smart_long(drive, state, lock, poll)
        surface = run_surface_test(drive, state, lock, batch_dir / f"{Path(drive['dev']).name}.surface.log", poll)

        begin_stage(state, drive['id'], 'final-smart', 'Capturing final SMART')
        save_state(state, lock)
        final_text = smart_text(drive['dev'], ['-x'])
        (batch_dir / f"{drive['serial']}.after.smart.txt").write_text(final_text)
        final = _smart_record(drive, final_text)
        complete_stage(state, drive['id'], 'final-smart')
        save_state(state, lock)

        begin_stage(state, drive['id'], 'classify', 'Classifying qualification result')
        decision, reason = classify_qualification(baseline, final, surface)
        complete_stage(state, drive['id'], 'classify')
        result = 'BAD' if decision == 'REJECTED' else decision
        finish_drive(state, drive['id'], result, reason)
        update_drive(state, drive['id'], workflow_status=decision, surface_result=surface)
        save_state(state, lock)
    except Exception as exc:
        finish_drive(state, drive['id'], 'BAD', f'Qualification worker failed: {exc}')
        update_drive(state, drive['id'], workflow_status='REJECTED')
        save_state(state, lock)


def run_batch(poll, allow_existing_data=False):
    drives = discover()
    if not drives:
        raise SystemExit('No eligible non-OS, unmounted disks found.')

    batch_id = 'qualify_' + datetime.now(timezone.utc).strftime('%Y-%m-%d_%H-%M-%S')
    batch_dir = REPORTS / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)
    state = create_batch_state(batch_id, drives)
    lock = threading.Lock()
    accepted = []

    for drive in drives:
        decision = drive.get('precheck')
        if decision == 'REJECT':
            reject_drive(state, drive['id'], drive.get('precheck_reason', 'SMART precheck failed'))
        elif decision == 'PROTECTED' and not allow_existing_data:
            reject_drive(state, drive['id'], 'PROTECTED: existing partitions/filesystems detected; rerun with --allow-existing-data only if erasure is intentional')
        else:
            accepted.append(drive)

    save_state(state, lock)
    fields = ['dev', 'serial', 'model', 'size_bytes', 'health', 'reallocated', 'pending', 'uncorrectable', 'precheck', 'precheck_reason']
    with open(batch_dir / 'precheck.csv', 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows([{k: d.get(k, '') for k in fields} for d in drives])

    if not accepted:
        state['status'] = 'COMPLETE'
        state['ended_utc'] = datetime.now(timezone.utc).isoformat()
        save_state(state, lock)
        return

    threads = [threading.Thread(target=qualify_drive, args=(d, state, lock, batch_dir, poll), name=d['serial']) for d in accepted]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    state['status'] = 'COMPLETE'
    state['ended_utc'] = datetime.now(timezone.utc).isoformat()
    save_state(state, lock)


def main():
    parser = argparse.ArgumentParser(prog='diskqual qualification worker')
    parser.add_argument('--yes', action='store_true')
    parser.add_argument('--poll', type=int, default=10)
    parser.add_argument('--allow-existing-data', action='store_true', help='Allow destructive qualification of disks that contain existing partitions/filesystems.')
    args = parser.parse_args()
    if not args.yes:
        raise SystemExit('Refusing destructive qualification without --yes.')
    run_batch(args.poll, allow_existing_data=args.allow_existing_data)


if __name__ == '__main__':
    main()
