# qualification_policy.py


def _as_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


REALLOC_GROWTH_REJECT = 8
SURFACE_ANOMALY_REVIEW_LIMIT = 8


def smart_snapshot(drive):
    return {
        'health': str(drive.get('health') or 'UNKNOWN').strip(),
        'reallocated': _as_int(drive.get('reallocated')),
        'pending': _as_int(drive.get('pending')),
        'uncorrectable': _as_int(drive.get('uncorrectable')),
    }


def classify_qualification(baseline, final, surface=None):
    """Classify a fully tested used drive using a balanced reuse policy.

    Historical defects may earn REVIEW when they remain stable. Progressive
    deterioration, data corruption, or inability to complete the surface test
    is REJECTED.
    """
    before = smart_snapshot(baseline or {})
    after = smart_snapshot(final or {})
    surface = surface or {}

    reasons = []
    review = []

    health = after['health'].upper()
    if health not in ('OK', 'PASSED'):
        reasons.append(f"SMART health: {after['health']}")

    if surface.get('fatal'):
        reasons.append(str(surface.get('fatal_reason') or 'fatal surface-test error'))
    if int(surface.get('corruption_errors') or 0) > 0:
        reasons.append(f"{int(surface.get('corruption_errors') or 0)} verification mismatch/corruption error(s)")
    if not surface.get('completed', False):
        reasons.append('surface test did not complete')

    pending_growth = after['pending'] - before['pending']
    uncorrectable_growth = after['uncorrectable'] - before['uncorrectable']
    realloc_growth = after['reallocated'] - before['reallocated']

    if after['pending'] > 0 and (before['pending'] == 0 or pending_growth > 0):
        reasons.append(f"pending sectors increased to {after['pending']}")
    elif after['pending'] > 0:
        review.append(f"{after['pending']} historical pending sector(s) remained stable")

    if after['uncorrectable'] > 0 and (before['uncorrectable'] == 0 or uncorrectable_growth > 0):
        reasons.append(f"uncorrectable sectors increased to {after['uncorrectable']}")
    elif after['uncorrectable'] > 0:
        review.append(f"{after['uncorrectable']} historical uncorrectable sector(s) remained stable")

    if realloc_growth >= REALLOC_GROWTH_REJECT:
        reasons.append(f"reallocated sectors increased by {realloc_growth} ({before['reallocated']} -> {after['reallocated']})")
    elif realloc_growth > 0:
        review.append(f"reallocated sectors increased by {realloc_growth} ({before['reallocated']} -> {after['reallocated']})")
    elif after['reallocated'] > 0:
        review.append(f"{after['reallocated']} historical reallocated sectors remained stable")

    anomalies = int(surface.get('recoverable_errors') or 0)
    if anomalies > SURFACE_ANOMALY_REVIEW_LIMIT:
        reasons.append(f'{anomalies} recoverable surface I/O anomalies exceeded reuse threshold')
    elif anomalies > 0:
        review.append(f'{anomalies} recoverable surface I/O anomaly/anomalies')

    if reasons:
        return 'REJECTED', '; '.join(reasons + review)
    if review:
        return 'REVIEW', '; '.join(review)
    return 'QUALIFIED', 'Full qualification completed without detected deterioration or surface errors'
