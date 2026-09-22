"""Read-only advisory state; never controls devices or processes."""
def classify(metric, reservation, reservations_known=True, vram_threshold=256, util_threshold=5):
    if not metric.get('reachable'):
        return 'OFFLINE'
    if not reservations_known:
        return 'UNKNOWN'
    processes = metric.get('processes')
    vram, average = metric.get('vram_used_mib'), metric.get('util_avg')
    busy = bool(processes) or (vram is not None and vram > vram_threshold) or (average is not None and average >= util_threshold)
    if not busy:
        # Missing process collector or warm-up history must never imply borrowable.
        if processes is None or vram is None or average is None or not metric.get('history_complete', False):
            return 'UNKNOWN'
        return 'RESERVED_IDLE' if reservation else 'FREE'
    if not reservation:
        return 'UNRESERVED_IN_USE'
    if not processes or not reservation.get('owner_linux'):
        return 'UNKNOWN'
    owners = {p['user'] for p in processes}
    owner = reservation['owner_linux']
    if owners == {owner}:
        return 'RESERVED_IN_USE'
    if owner in owners:
        return 'CONFLICT'
    return 'BORROWED'
