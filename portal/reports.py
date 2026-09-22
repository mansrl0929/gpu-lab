"""Long-window operating report: aggregates only, for sizing decisions, not grading."""
from datetime import datetime

SATURATION_PERCENT = 85


def _parse(value, tzinfo=None):
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=tzinfo)


def summarize_reservations(reservations, start, end, users=()):
    """Reservation-side aggregates over a window; clipped to the window edges."""
    names = {u['linux_username']: u.get('display_name') for u in users if u.get('linux_username')}
    rows, by_user = [], {}
    for reservation in reservations:
        first, last = _parse(reservation['start'], start.tzinfo), _parse(reservation['end'], start.tzinfo)
        overlap = (min(last, end) - max(first, start)).total_seconds() / 3600
        if overlap <= 0:
            continue
        gpu_hours = overlap * len(reservation['resources'])
        rows.append({'multi': len(reservation['resources']) > 1, 'hours': (last - first).total_seconds() / 3600,
                     'gpu_hours': gpu_hours})
        key = reservation.get('owner_linux') or reservation.get('owner_name') or '확인 필요'
        entry = by_user.setdefault(key, {'user': key, 'name': names.get(key) or reservation.get('owner_name') or key,
                                         'reserved_gpu_hours': 0.0, 'reservations': 0})
        entry['reserved_gpu_hours'] += gpu_hours
        entry['reservations'] += 1
    multi = sum(1 for row in rows if row['multi'])
    return {
        'total': len(rows),
        'multi_gpu': multi,
        'multi_gpu_percent': round(100 * multi / len(rows), 1) if rows else None,
        'reserved_gpu_hours': round(sum(row['gpu_hours'] for row in rows), 1),
        'average_hours': round(sum(row['hours'] for row in rows) / len(rows), 1) if rows else None,
        'by_user': sorted(({**entry, 'reserved_gpu_hours': round(entry['reserved_gpu_hours'], 1)}
                           for entry in by_user.values()), key=lambda row: -row['reserved_gpu_hours']),
    }


def merge_user_hours(reserved, observed, users=()):
    """Join reserved GPU-hours with GPU-hours actually observed by the process collector."""
    names = {u['linux_username']: u.get('display_name') for u in users if u.get('linux_username')}
    merged = {row['user']: dict(row) for row in reserved}
    for user, hours in (observed or {}).items():
        row = merged.setdefault(user, {'user': user, 'name': names.get(user) or user,
                                       'reserved_gpu_hours': 0.0, 'reservations': 0})
        row['used_gpu_hours'] = hours
    for row in merged.values():
        row.setdefault('used_gpu_hours', None)
    return sorted(merged.values(), key=lambda row: -(row['used_gpu_hours'] or row['reserved_gpu_hours'] or 0))


def hourly_profile(series, tzinfo, threshold=SATURATION_PERCENT, step_hours=1):
    """Average a range query by hour of day in KST and count saturated hours."""
    buckets = {hour: [] for hour in range(24)}
    saturated = 0
    for stamp, value in series:
        buckets[datetime.fromtimestamp(stamp, tzinfo).hour].append(value)
        saturated += value >= threshold
    return {'profile': [{'hour': hour, 'value': round(sum(values) / len(values), 1) if values else None}
                        for hour, values in buckets.items()],
            'saturated_hours': round(saturated * step_hours, 1),
            'peak_hour': max((h for h in buckets if buckets[h]), key=lambda h: sum(buckets[h]) / len(buckets[h]), default=None),
            'threshold_percent': threshold}
