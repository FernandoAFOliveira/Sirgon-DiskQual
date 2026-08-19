# reconcile.py
import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from .cli import discover, parse_attrs, parse_field, smart_text
from .precheck import classify_precheck
from .station import JOBS, active_serials, load_drive_workflow, save_drive_workflow

BASE = Path(os.environ.get('DISKQUAL_HOME', '/opt/diskqual'))
REPORTS = BASE / 'reports'


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _latest_extended_selftest(text):
    """Return (passed, normalized_line) for the newest Extended SMART test."""
    for raw in text.splitlines():
        if not re.match(r'^\s*#\s*\d+\s+', raw):
            continue
        if not re.search(r'\bExtended\b', raw, re.I):
            continue
        line = ' '.join(raw.split())
        lower = line.lower()
        if 'completed without error' in lower or 'completed successfully' in lower:
            return True, line
        return False, line
    return None, 'No completed Extended SMART self-test was found.'


def _execution_status(text):
    match = re.search(r'Self-test execution status:\s+\(\s*(\d+)\)\s*(.*)', text)
    if not match:
        return None, ''
    return int(match.group(1)), ' '.join(match.group(2).split())


def _diskqual_long_evidence(serial):
    """Find strong persisted evidence that DiskQual started and followed a long test."""
    best = None
    if not JOBS.exists():
        return None
    for path in JOBS.glob('smart-long-*.json'):
        try:
            state = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        drive = (state.get('drives') or {}).get(serial)
        if not isinstance(drive, dict):
            continue
        if str(drive.get('stage') or '') != 'smart-long':
            continue
        progress = float(drive.get('stage_progress') or 0)
        elapsed = int(drive.get('stage_elapsed_seconds') or 0)
        if progress < 0.95 or elapsed <= 0:
            continue
        row = {
            'path': str(path),
            'progress': progress,
            'elapsed': elapsed,
            'started': drive.get('stage_started_utc'),
            'updated': state.get('updated_utc'),
        }
        if best is None or str(row.get('updated') or '') > str(best.get('updated') or ''):
            best = row
    return best


def reconcile_drive(drive):
    serial = str(drive.get('serial') or drive.get('id') or '')
    dev = str(drive.get('dev') or '')
    if not serial or not dev:
        return {'serial': serial or '?', 'dev': dev or '?', 'decision': 'SKIPPED', 'detail': 'Missing serial or device path.'}

    selftest_text = smart_text(dev, ['-l', 'selftest'])
    passed, selftest_line = _latest_extended_selftest(selftest_text)

    current_text = smart_text(dev, ['-a'])
    if passed is None:
        code, execution_detail = _execution_status(current_text)
        evidence = _diskqual_long_evidence(serial)
        if code == 0 and evidence and serial not in active_serials():
            passed = True
            selftest_line = (
                f"DiskQual observed SMART Long through {evidence['progress'] * 100:.0f}% "
                f"({evidence['elapsed']} s elapsed); drive now reports execution status 0"
            )
            if execution_detail:
                selftest_line += f': {execution_detail}'
            selftest_line += '; self-test log entry unavailable'
        else:
            return {'serial': serial, 'dev': dev, 'decision': 'UNCHANGED', 'detail': selftest_line}

    attrs = parse_attrs(current_text)
    health = parse_field(current_text, ['SMART overall-health self-assessment test result', 'SMART Health Status']) or 'UNKNOWN'
    precheck, precheck_reason = classify_precheck({**drive, **attrs, 'health': health})

    if not passed:
        status = 'REJECTED'
        smart_result = 'FAIL'
        detail = selftest_line
    elif precheck == 'REJECT':
        status = 'REJECTED'
        smart_result = 'PASS'
        detail = f'{selftest_line}; current SMART policy rejects drive: {precheck_reason}'
    else:
        status = 'READY_FOR_SURFACE'
        smart_result = 'PASS'
        detail = selftest_line
        if precheck == 'REVIEW':
            detail += f'; baseline review: {precheck_reason}'

    current = dict(load_drive_workflow(serial))
    current.update({
        'serial': serial,
        'dev': dev,
        'model': drive.get('model', ''),
        'size_bytes': drive.get('size_bytes', 0),
        'status': status,
        'smart_long_result': smart_result,
        'smart_long_detail': detail,
        'smart_long_utc': _utc_now(),
        'smart_reconciled': True,
        'smart_reconciled_utc': _utc_now(),
        'baseline_precheck': precheck,
        'baseline_precheck_reason': precheck_reason,
    })
    save_drive_workflow(serial, current)

    return {
        'serial': serial,
        'dev': dev,
        'decision': status,
        'smart_long_result': smart_result,
        'precheck': precheck,
        'detail': detail,
    }


def reconcile_all():
    drives = discover()
    results = [reconcile_drive(drive) for drive in drives]

    report_dir = REPORTS / ('reconcile_smart_' + datetime.now(timezone.utc).strftime('%Y-%m-%d_%H-%M-%S'))
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / 'summary.json').write_text(json.dumps({'created_utc': _utc_now(), 'results': results}, indent=2))

    return results, report_dir


def main():
    parser = argparse.ArgumentParser(
        prog='diskqual-reconcile',
        description='Reconcile completed SMART Long results into Sirgon DiskQual workflow state without rerunning tests.',
    )
    parser.parse_args()

    results, report_dir = reconcile_all()
    ready = sum(1 for row in results if row.get('decision') == 'READY_FOR_SURFACE')
    rejected = sum(1 for row in results if row.get('decision') == 'REJECTED')
    unchanged = len(results) - ready - rejected

    print('SIRGON DISKQUAL — SMART RECONCILIATION')
    for row in results:
        dev = Path(str(row.get('dev') or '?')).name
        print(f"{dev:<6} {row.get('serial', '?'):<18} {row.get('decision', '?'):<18} {row.get('detail', '')}")
    print()
    print(f'Ready for surface: {ready}')
    print(f'Rejected:          {rejected}')
    print(f'Unchanged/skipped: {unchanged}')
    print(f'Report: {report_dir}')


if __name__ == '__main__':
    main()
