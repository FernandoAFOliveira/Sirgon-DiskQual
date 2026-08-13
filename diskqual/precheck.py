# precheck.py

from .devices import has_existing_layout


def _as_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def classify_precheck(drive):
    dev = str(drive.get('dev') or '')
    if dev and has_existing_layout(dev):
        return 'PROTECTED', 'Existing partition table or filesystem detected'

    health = str(drive.get('health') or 'UNKNOWN').strip()
    health_upper = health.upper()
    realloc = _as_int(drive.get('reallocated'))
    pending = _as_int(drive.get('pending'))
    uncorrectable = _as_int(drive.get('uncorrectable'))

    # A failed overall SMART health assessment is still a hard precheck reject.
    # Historical sector defects, however, are allowed into qualification as
    # REVIEW candidates so a destructive surface pass can determine whether
    # the drive is stable or actively deteriorating.
    if health_upper not in ('OK', 'PASSED'):
        return 'REJECT', f'SMART health: {health}'

    review = []
    if realloc > 0:
        review.append(f'{realloc} reallocated sectors')
    if pending > 0:
        review.append(f'{pending} pending sectors')
    if uncorrectable > 0:
        review.append(f'{uncorrectable} uncorrectable sectors')

    if review:
        return 'REVIEW', '; '.join(review)

    return 'PASS', 'Baseline SMART acceptable'
