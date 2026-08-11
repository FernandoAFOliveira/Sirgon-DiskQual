# precheck.py

def _as_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def classify_precheck(drive):
    health = str(drive.get('health') or 'UNKNOWN').strip()
    health_upper = health.upper()
    realloc = _as_int(drive.get('reallocated'))
    pending = _as_int(drive.get('pending'))
    uncorrectable = _as_int(drive.get('uncorrectable'))

    reasons = []
    rejected = False

    if health_upper not in ('OK', 'PASSED'):
        rejected = True
        reasons.append(f'SMART health: {health}')

    if pending > 0:
        rejected = True
        reasons.append(f'{pending} pending sectors')

    if uncorrectable > 0:
        rejected = True
        reasons.append(f'{uncorrectable} uncorrectable sectors')

    if rejected:
        return 'REJECT', '; '.join(reasons)

    if realloc > 0:
        return 'REVIEW', f'{realloc} reallocated sectors'

    return 'PASS', 'Baseline SMART acceptable'
