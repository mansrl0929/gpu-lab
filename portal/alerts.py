"""Advisory notices only. Nothing here stops a process or changes GPU permissions."""
from datetime import datetime, timedelta

LEVELS = {'danger': 0, 'warning': 1, 'info': 2}
STARTING_SOON = timedelta(minutes=30)
OVERRUN_WINDOW = timedelta(hours=2)


def _parse(value, tzinfo=None):
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=tzinfo)


def _users(metric):
    return {p['user'] for p in metric.get('processes') or []}


def build_alerts(gpus, reservations, timestamp, errors=(), users=()):
    """Return the notices shown in the portal, most urgent first.

    Covers the operating guide's reference notices: a reserved GPU used by
    someone else, a reservation that ended while the work keeps running,
    offline servers, and states the collectors cannot confirm.
    """
    names = {u['linux_username']: u.get('display_name') for u in users if u.get('linux_username')}
    show = lambda user: names.get(user) or user
    alerts = [{'level': 'danger', 'resource': None, 'title': '연동 오류', 'message': message} for message in errors]
    for gpu in gpus:
        state, metric, reservation = gpu['state'], gpu.get('metrics') or {}, gpu.get('reservation')
        if state == 'CONFLICT':
            alerts.append({'level': 'danger', 'resource': gpu['id'], 'title': '사용 조정 필요',
                           'message': '예약과 실제 사용이 겹칩니다. 사용자끼리 확인하세요. 시스템은 작업을 중단하지 않습니다.'})
        elif state == 'BORROWED':
            started = reservation and timestamp - _parse(reservation['start'], timestamp.tzinfo) <= STARTING_SOON
            owner = (reservation or {}).get('owner_name') or (reservation or {}).get('owner_linux') or '예약자'
            alerts.append({'level': 'warning', 'resource': gpu['id'],
                           'title': '예약 시간에 다른 사용자 사용 중' if started else '대여 사용 중',
                           'message': f'{owner}님의 예약 시간이지만 {", ".join(sorted(show(u) for u in _users(metric))) or "다른 사용자"}님이 사용 중입니다. '
                                      '반납 시점을 함께 정하세요.'})
        elif state == 'OFFLINE':
            alerts.append({'level': 'warning', 'resource': gpu['id'], 'title': '오프라인',
                           'message': '최근 GPU 메트릭이 없습니다. 서버 전원·네트워크·exporter를 확인하세요.'})
        elif state == 'UNKNOWN':
            alerts.append({'level': 'info', 'resource': gpu['id'], 'title': '확인 필요',
                           'message': '예약 또는 실사용 정보를 확인할 수 없습니다. 사용 가능 여부를 직접 확인하세요.'})
        if state == 'UNRESERVED_IN_USE' and not reservation:
            running = _users(metric)
            ended = [r for r in reservations if gpu['id'] in r['resources'] and running and r.get('owner_linux') in running
                     and timedelta(0) <= timestamp - _parse(r['end'], timestamp.tzinfo) <= OVERRUN_WINDOW]
            if ended:
                last = max(ended, key=lambda r: r['end'])
                alerts.append({'level': 'warning', 'resource': gpu['id'], 'title': '예약 종료 후 사용 지속',
                               'message': f'{last.get("owner_name") or last["owner_linux"]}님의 예약이 '
                                          f'{_parse(last["end"], timestamp.tzinfo).strftime("%H:%M")}에 끝났지만 작업이 계속 실행 중입니다. '
                                          '계속 사용한다면 예약을 연장하세요.'})
    return sorted(alerts, key=lambda a: LEVELS[a['level']])
