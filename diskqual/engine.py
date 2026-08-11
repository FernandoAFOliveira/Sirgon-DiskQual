# engine.py
import argparse
import csv
import os
import re
import selectors
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .cli import discover, parse_attrs, parse_field, selftest_line, selftest_status, smart_text
from .precheck import classify_precheck
from .progress import (
    atomic_write_json,
    begin_stage,
    complete_stage,
    create_batch_state,
    fail_drive,
    finish_drive,
    reject_drive,
    update_drive,
)

BASE = Path(os.environ.get('DISKQUAL_HOME', '/opt/diskqual'))
REPORTS = BASE / 'reports'
STATE = BASE / 'state.json'

SURFACE_LOG_LIMIT = 16 * 1024 * 1024
SURFACE_RECENT_LIMIT = 256 * 1024
SURFACE_ERROR_LIMIT = 100
SURFACE_ERROR_MARKERS = (
    b'Invalid argument during seek',
    b'Input/output error',
    b'I/O error',
)


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
        update_drive(
            state,
            drive['id'],
            stage_progress=progress,
            stage_eta_seconds=eta,
            message=status or 'SMART extended self-test running',
        )
        save_state(state, lock)
        lower = (status or '').lower()
        if status and not any(x in lower for x in ('remaining', 'progress', 'self-test routine in progress')):
            break
        if selftest_line(text) and elapsed > 30 and not status:
            break

    complete_stage(state, drive['id'], 'smart-long', 'SMART extended self-test complete')
    save_state(state, lock)


def surface_progress(text):
    normalized = text.replace('\r', '\n')
    testing_pos = normalized.rfind('Testing with pattern')
    compare_pos = normalized.rfind('Reading and comparing')

    if compare_pos > testing_pos:
        phase_text = normalized[compare_pos:]
        matches = re.findall(r'(\d+(?:\.\d+)?)% done', phase_text)
        pct = float(matches[-1]) / 100.0 if matches else (1.0 if 'done' in phase_text else 0.0)
        return min(0.999, 0.5 + 0.5 * pct), 'Reading and comparing'

    phase_text = normalized[testing_pos:] if testing_pos >= 0 else normalized
    matches = re.findall(r'(\d+(?:\.\d+)?)% done', phase_text)
    pct = float(matches[-1]) / 100.0 if matches else (1.0 if 'Testing with pattern 0x00: done' in normalized else 0.0)
    return min(0.5, 0.5 * pct), 'Writing pattern 0x00'


def _append_recent(recent, chunk):
    recent.extend(chunk)
    if len(recent) > SURFACE_RECENT_LIMIT:
        del recent[:-SURFACE_RECENT_LIMIT]


def _write_bounded(log, chunk, written, truncated):
    if written >= SURFACE_LOG_LIMIT:
        return written, True
    remaining = SURFACE_LOG_LIMIT - written
    part = chunk[:remaining]
    if part:
        log.write(part)
        written += len(part)
    if len(chunk) > len(part) and not truncated:
        marker = b'\n\n[Sirgon DiskQual: surface log truncated at 16 MiB; additional output discarded]\n'
        log.write(marker)
        written += len(marker)
        truncated = True
    return written, truncated


def _error_count(chunk):
    return sum(chunk.count(marker) for marker in SURFACE_ERROR_MARKERS)


def run_surface_test(drive, state, lock, log_path, poll):
    begin_stage(state, drive['id'], 'surface-test', 'Writing pattern 0x00')
    save_state(state, lock)
    start = time.monotonic()
    total_work = max(1, int(drive['size_bytes'])) * 2
    cmd = ['badblocks', '-wsv', '-b', '4096', '-c', '16384', '-t', '0x00', drive['dev']]

    recent = bytearray()
    error_count = 0
    written = 0
    truncated = False
    abort_reason = None
    last_update = 0.0

    with open(log_path, 'wb') as log:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
        selector = selectors.DefaultSelector()
        selector.register(proc.stdout, selectors.EVENT_READ)

        while True:
            events = selector.select(timeout=1.0)
            for key, _mask in events:
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                _append_recent(recent, chunk)
                written, truncated = _write_bounded(log, chunk, written, truncated)
                error_count += _error_count(chunk)
                if error_count >= SURFACE_ERROR_LIMIT and abort_reason is None:
                    abort_reason = f'Surface test aborted after {error_count}+ repeated seek/I/O errors'
                    proc.terminate()

            now = time.monotonic()
            if now - last_update >= max(1, poll):
                text = recent.decode(errors='replace')
                progress, message = surface_progress(text)
                elapsed = max(1, now - start)
                throughput = total_work * progress / elapsed / (1024 * 1024) if progress else None
                eta = int(elapsed * (1 - progress) / progress) if progress else None
                if error_count:
                    message = f'{message} — {error_count} surface I/O errors observed'
                update_drive(
                    state,
                    drive['id'],
                    stage_progress=progress,
                    stage_eta_seconds=eta,
                    throughput_mib_s=throughput,
                    message=message,
                )
                save_state(state, lock)
                last_update = now

            if proc.poll() is not None and not selector.get_map():
                break

        selector.close()
        rc = proc.wait()
        log.flush()

    text = recent.decode(errors='replace').replace('\r', '\n')
    if abort_reason:
        raise RuntimeError(f'{abort_reason}; see bounded evidence log {log_path}')
    if rc != 0:
        raise RuntimeError(f'badblocks exited with status {rc}; see {log_path}')
    if 'Pass completed, 0 bad blocks found.' not in text:
        raise RuntimeError(f'surface test did not report a clean pass; see {log_path}')

    complete_stage(state, drive['id'], 'surface-test', 'Full write/read-compare surface test complete')
    save_state(state, lock)


def qualify_drive(drive, state, lock, batch_dir, poll):
    try:
        begin_stage(state, drive['id'], 'baseline-smart', 'Capturing baseline SMART')
        save_state(state, lock)
        (batch_dir / f"{drive['serial']}.before.smart.txt").write_text(smart_text(drive['dev'], ['-x']))
        complete_stage(state, drive['id'], 'baseline-smart')
        save_state(state, lock)

        begin_stage(state, drive['id'], 'smart-short', 'SMART short self-test')
        save_state(state, lock)
        smart_text(drive['dev'], ['-t', 'short'])
        time.sleep(90)
        complete_stage(state, drive['id'], 'smart-short', 'SMART short test interval complete')
        save_state(state, lock)

        wait_smart_long(drive, state, lock, poll)
        run_surface_test(drive, state, lock, batch_dir / f"{Path(drive['dev']).name}.surface.log", poll)

        begin_stage(state, drive['id'], 'final-smart', 'Capturing final SMART')
        save_state(state, lock)
        final = smart_text(drive['dev'], ['-x'])
        (batch_dir / f"{drive['serial']}.after.smart.txt").write_text(final)
        complete_stage(state, drive['id'], 'final-smart')
        save_state(state, lock)

        begin_stage(state, drive['id'], 'classify', 'Classifying result')
        attrs = parse_attrs(final)
        health = parse_field(final, ['SMART overall-health self-assessment test result', 'SMART Health Status']) or 'UNKNOWN'
        decision, reason = classify_precheck({**drive, **attrs, 'health': health})
        result = 'BAD' if decision == 'REJECT' else decision
        complete_stage(state, drive['id'], 'classify')
        finish_drive(state, drive['id'], result, reason)
        save_state(state, lock)
    except Exception as exc:
        fail_drive(state, drive['id'], str(exc))
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
            reject_drive(
                state,
                drive['id'],
                'PROTECTED: existing partitions/filesystems detected; rerun with --allow-existing-data only if erasure is intentional',
            )
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

    threads = [
        threading.Thread(target=qualify_drive, args=(d, state, lock, batch_dir, poll), name=d['serial'])
        for d in accepted
    ]
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
    parser.add_argument(
        '--allow-existing-data',
        action='store_true',
        help='Allow destructive qualification of disks that contain existing partitions/filesystems.',
    )
    args = parser.parse_args()
    if not args.yes:
        raise SystemExit('Refusing destructive qualification without --yes.')
    run_batch(args.poll, allow_existing_data=args.allow_existing_data)


if __name__ == '__main__':
    main()
