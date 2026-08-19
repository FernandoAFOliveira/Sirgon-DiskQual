# surface.py
import json
import os
import re
import selectors
import subprocess
import time
from pathlib import Path

from .identity import resolve_serial_device
from .progress import atomic_write_json, begin_stage, complete_stage, update_drive

MIN_CHUNK_BYTES = 1 * 1024**3
SECOND_CHUNK_BYTES = 2 * 1024**3
CHUNK_GROWTH_BYTES = 2 * 1024**3
MAX_CHUNK_BYTES = 32 * 1024**3
TARGET_CHUNK_COUNT = 256
BLOCK_SIZE = 4096
BADBLOCKS_IO_BLOCKS = 16384
SURFACE_LOG_LIMIT = 16 * 1024 * 1024
SURFACE_RECENT_LIMIT = 256 * 1024
RECOVERABLE_ERROR_LIMIT = 8
FATAL_REPEAT_LIMIT = 100
FATAL_MARKERS = (
    b'Invalid argument during seek',
    b'No such device',
    b'Device or resource busy',
)


def _clamp(value, low, high):
    return max(low, min(high, value))


def target_chunk_size(size_bytes):
    """Return the healthy-drive chunk ceiling for this capacity."""
    target = max(BLOCK_SIZE, int(size_bytes) // TARGET_CHUNK_COUNT)
    target = _clamp(target, MIN_CHUNK_BYTES, MAX_CHUNK_BYTES)
    return max(BLOCK_SIZE, (target // BLOCK_SIZE) * BLOCK_SIZE)


def next_clean_chunk_size(current, target):
    """Grow cautiously after a fully clean write+verify chunk."""
    current = max(MIN_CHUNK_BYTES, int(current))
    target = _clamp(int(target), MIN_CHUNK_BYTES, MAX_CHUNK_BYTES)
    if current >= target:
        return target
    if current <= MIN_CHUNK_BYTES:
        return min(target, SECOND_CHUNK_BYTES)
    return min(target, current + CHUNK_GROWTH_BYTES)


def _checkpoint_paths(log_path):
    stem = Path(log_path).with_suffix('')
    return Path(str(stem) + '.state-A.json'), Path(str(stem) + '.state-B.json')


def _save_checkpoint(log_path, payload):
    a, b = _checkpoint_paths(log_path)
    sequence = int(payload.get('sequence') or 0)
    atomic_write_json(a if sequence % 2 == 0 else b, payload)


def _append_recent(recent, chunk):
    recent.extend(chunk)
    if len(recent) > SURFACE_RECENT_LIMIT:
        del recent[:-SURFACE_RECENT_LIMIT]


def _bounded_write(log, data, written):
    if written >= SURFACE_LOG_LIMIT:
        return written
    part = data[: SURFACE_LOG_LIMIT - written]
    if part:
        log.write(part)
        written += len(part)
    return written


def _parse_badblocks_summary(text):
    match = re.search(
        r'Pass completed,\s*(\d+) bad blocks found\.\s*\((\d+)/(\d+)/(\d+) errors\)',
        text,
        re.I,
    )
    if not match:
        return None
    bad, read_errors, write_errors, corruption_errors = (int(value) for value in match.groups())
    return {
        'bad_blocks': bad,
        'read_errors': read_errors,
        'write_errors': write_errors,
        'corruption_errors': corruption_errors,
    }


def _run_chunk(dev, first_block, last_block, log, written):
    cmd = [
        'badblocks', '-wsv', '-b', str(BLOCK_SIZE), '-c', str(BADBLOCKS_IO_BLOCKS),
        '-t', '0x00', dev, str(last_block), str(first_block),
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    recent = bytearray()
    fatal_hits = 0

    while True:
        events = selector.select(timeout=1.0)
        for key, _mask in events:
            data = os.read(key.fileobj.fileno(), 65536)
            if not data:
                selector.unregister(key.fileobj)
                continue
            _append_recent(recent, data)
            written = _bounded_write(log, data, written)
            fatal_hits += sum(data.count(marker) for marker in FATAL_MARKERS)
            if fatal_hits >= FATAL_REPEAT_LIMIT:
                proc.terminate()

        if proc.poll() is not None and not selector.get_map():
            break

    selector.close()
    rc = proc.wait()
    text = recent.decode(errors='replace').replace('\r', '\n')
    summary = _parse_badblocks_summary(text)
    return rc, summary, fatal_hits, text, written


def run_adaptive_surface_test(drive, state, lock, log_path, poll, save_state):
    """Destructively write/read-verify a drive in adaptive bounded chunks.

    The drive serial is authoritative. /dev/sdX is re-resolved and independently
    verified immediately before every destructive chunk, so a Linux device-name
    reassignment cannot redirect a later chunk to another disk.
    """
    serial = str(drive['serial'])
    initial_dev = resolve_serial_device(serial)
    size_bytes = int(drive['size_bytes'])
    total_blocks = size_bytes // BLOCK_SIZE
    if total_blocks <= 0:
        raise RuntimeError('Drive capacity is invalid for surface testing')

    target = target_chunk_size(size_bytes)
    chunk_size = MIN_CHUNK_BYTES
    verified_bytes = 0
    sequence = 0
    clean_streak = 0
    recoverable_errors = 0
    corruption_errors = 0
    fatal_reason = ''
    started = time.monotonic()
    written = 0
    last_dev = initial_dev

    begin_stage(state, drive['id'], 'surface-test', f'Adaptive surface test; target chunk {target / 1024**3:.1f} GiB')
    update_drive(state, drive['id'], dev=initial_dev)
    save_state(state, lock)

    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, 'ab') as log:
        written = min(log.tell(), SURFACE_LOG_LIMIT)
        header = (
            f'\n[DiskQual adaptive surface start] serial={serial} dev={initial_dev} size={size_bytes} '
            f'target_chunk={target} min_chunk={MIN_CHUNK_BYTES} max_chunk={MAX_CHUNK_BYTES} '
            f'growth={CHUNK_GROWTH_BYTES}\n'
        ).encode()
        written = _bounded_write(log, header, written)

        first_block = 0
        while first_block < total_blocks:
            # Identity is re-resolved immediately before every destructive chunk.
            dev = resolve_serial_device(serial)
            last_dev = dev
            update_drive(state, drive['id'], dev=dev)
            save_state(state, lock)

            blocks_this_chunk = max(1, chunk_size // BLOCK_SIZE)
            last_block = min(total_blocks - 1, first_block + blocks_this_chunk - 1)
            actual_bytes = (last_block - first_block + 1) * BLOCK_SIZE
            sequence += 1

            event = (
                f'\n[chunk {sequence}] serial={serial} dev={dev} first_block={first_block} '
                f'last_block={last_block} bytes={actual_bytes} chunk_size={chunk_size}\n'
            ).encode()
            written = _bounded_write(log, event, written)
            log.flush()

            rc, summary, fatal_hits, recent_text, written = _run_chunk(dev, first_block, last_block, log, written)
            log.flush()

            if fatal_hits >= FATAL_REPEAT_LIMIT:
                fatal_reason = f'repeated fatal device/seek errors ({fatal_hits}+ occurrences)'
            elif rc != 0:
                fatal_reason = f'badblocks exited with status {rc} in chunk {sequence}'
            elif summary is None:
                fatal_reason = f'badblocks did not produce a valid completion summary for chunk {sequence}'

            if fatal_reason:
                break

            read_errors = int(summary['read_errors'])
            write_errors = int(summary['write_errors'])
            chunk_corruption = int(summary['corruption_errors'])
            chunk_recoverable = read_errors + write_errors
            recoverable_errors += chunk_recoverable
            corruption_errors += chunk_corruption

            verified_bytes = min(size_bytes, (last_block + 1) * BLOCK_SIZE)
            progress = min(1.0, verified_bytes / max(1, size_bytes))
            elapsed = max(1.0, time.monotonic() - started)
            throughput = verified_bytes / elapsed / (1024 * 1024)
            eta = int(elapsed * (1.0 - progress) / progress) if progress > 0 else None

            if chunk_corruption > 0:
                fatal_reason = f'{chunk_corruption} data verification mismatch/corruption error(s) in chunk {sequence}'
            elif recoverable_errors > RECOVERABLE_ERROR_LIMIT:
                fatal_reason = f'{recoverable_errors} recoverable surface I/O anomalies exceeded threshold {RECOVERABLE_ERROR_LIMIT}'

            if chunk_recoverable or chunk_corruption:
                clean_streak = 0
                chunk_size = MIN_CHUNK_BYTES
                message = f'Chunk {sequence} verified with anomalies; next chunk reset to 1.0 GiB'
            else:
                clean_streak += 1
                chunk_size = next_clean_chunk_size(chunk_size, target)
                message = f'Chunk {sequence} verified clean; next chunk {chunk_size / 1024**3:.1f} GiB'

            update_drive(
                state,
                drive['id'],
                stage_progress=progress,
                stage_eta_seconds=eta,
                throughput_mib_s=throughput,
                message=message,
                surface_verified_bytes=verified_bytes,
                surface_chunk_size_bytes=chunk_size,
                surface_recoverable_errors=recoverable_errors,
                surface_corruption_errors=corruption_errors,
            )
            save_state(state, lock)

            checkpoint = {
                'version': 2,
                'sequence': sequence,
                'serial': serial,
                'dev_at_start': initial_dev,
                'dev_last_verified': last_dev,
                'size_bytes': size_bytes,
                'verified_bytes': verified_bytes,
                'next_first_block': last_block + 1,
                'chunk_size_bytes': chunk_size,
                'target_chunk_bytes': target,
                'clean_streak': clean_streak,
                'recoverable_errors': recoverable_errors,
                'corruption_errors': corruption_errors,
                'completed': False,
            }
            _save_checkpoint(log_path, checkpoint)

            if fatal_reason:
                break
            first_block = last_block + 1

        completed = not fatal_reason and verified_bytes >= size_bytes
        final_checkpoint = {
            'version': 2,
            'sequence': sequence + 1,
            'serial': serial,
            'dev_at_start': initial_dev,
            'dev_last_verified': last_dev,
            'size_bytes': size_bytes,
            'verified_bytes': verified_bytes,
            'next_first_block': total_blocks if completed else (verified_bytes // BLOCK_SIZE),
            'chunk_size_bytes': chunk_size,
            'target_chunk_bytes': target,
            'clean_streak': clean_streak,
            'recoverable_errors': recoverable_errors,
            'corruption_errors': corruption_errors,
            'completed': completed,
            'fatal_reason': fatal_reason,
        }
        _save_checkpoint(log_path, final_checkpoint)

        footer = (
            f'\n[DiskQual adaptive surface end] completed={completed} verified_bytes={verified_bytes} '
            f'recoverable_errors={recoverable_errors} corruption_errors={corruption_errors} '
            f'fatal={fatal_reason or "none"}\n'
        ).encode()
        _bounded_write(log, footer, written)
        log.flush()

    result = {
        'completed': completed,
        'verified_bytes': verified_bytes,
        'recoverable_errors': recoverable_errors,
        'corruption_errors': corruption_errors,
        'fatal': bool(fatal_reason),
        'fatal_reason': fatal_reason,
        'chunks_completed': sequence if completed else max(0, sequence - 1),
        'target_chunk_bytes': target,
        'dev_at_start': initial_dev,
        'dev_last_verified': last_dev,
    }

    if completed:
        complete_stage(state, drive['id'], 'surface-test', 'Adaptive full-surface write/read verification complete')
        save_state(state, lock)
    return result
