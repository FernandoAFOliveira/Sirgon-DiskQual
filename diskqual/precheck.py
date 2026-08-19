# precheck.py

from .devices import has_existing_layout


def _as_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _display_text(value):
    """Keep device-supplied text safe for Textual/Rich markup displays."""
    return str(value or '').replace('[', '(').replace(']', ')')


def classify_precheck(drive):
    dev = str(drive.get('dev') or '')
    if dev and has_existing_layout(dev):
        return 'PROTECTED', 'Existing partition table or filesystem detected'

    health = str(drive.get('health') or 'UNKNOWN').strip()
    health_upper = health.upper()
    realloc = _as_int(drive.get('reallocated'))
    pending = _as_int(drive.get('pending'))
    uncorrectable = _as_int(drive.get('uncorrectable'))

    # Only an explicit failed SMART health assessment is a hard precheck reject.
    # Some SAS/HBA combinations do not expose an ATA-style overall-health field;
    # that should be REVIEW so a non-destructive SMART Long test can still run.
    if health_upper in ('FAILED', 'FAIL', 'BAD') or health_upper.startswith('FAILED'):
        return 'REJECT', f'SMART health: {_display_text(health)}'

    review = []
    if health_upper not in ('OK', 'PASSED'):
        review.append(f'SMART health unavailable/indeterminate: {_display_text(health)}')
    if realloc > 0:
        review.append(f'{realloc} reallocated sectors')
    if pending > 0:
        review.append(f'{pending} pending sectors')
    if uncorrectable > 0:
        review.append(f'{uncorrectable} uncorrectable sectors')

    if review:
        return 'REVIEW', '; '.join(review)

    return 'PASS', 'Baseline SMART acceptable'
