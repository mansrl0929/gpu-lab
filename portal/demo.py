"""Sample data for the local preview. Never mixed into live readings."""
import math
from datetime import datetime, timedelta, timezone

from .reports import SATURATION_PERCENT

KST = timezone(timedelta(hours=9))

def now():
    return datetime.now(KST)

DEMO_MEMBERS = [('student1', '김민수'), ('student2', '이서연'), ('student3', '박지훈')]

# (title, project, owner, resources, start offset in hours, end offset in hours)
DEMO_SAMPLES = [
    ('RoMa 모델 학습', 'Sea Ice Drift', 'student1', ['AURORA-GPU0'], -2, 4),
    ('논문 실험 · ablation study', 'Vision Transformer', 'student2', ['AURORA-GPU1'], -1, 3),
    ('LLM fine-tuning', 'Language Model', 'student3', ['POLARIS-GPU0'], -2, 2),
    ('멀티 GPU 학습', 'Sea Ice Drift', 'student1', ['AURORA-GPU0', 'AURORA-GPU1'], 5, 9),
    ('데이터셋 임베딩', 'Retrieval', 'student2', ['POLARIS-GPU1'], 2, 5),
    # Past reservations give the operating report a window to aggregate.
    ('임베딩 추출', 'Retrieval', 'student2', ['AURORA-GPU2'], -6, -1),
    ('야간 학습', 'Vision Transformer', 'student1', ['AURORA-GPU3'], -12, -5),
    ('사전 실험 · augmentation', 'Sea Ice Drift', 'student2', ['AURORA-GPU1'], -30, -24),
    ('멀티 GPU 사전학습', 'Vision Transformer', 'student1', ['AURORA-GPU2', 'AURORA-GPU3'], -74, -50),
    ('추론 벤치마크', 'Retrieval', 'student3', ['POLARIS-GPU0'], -100, -92),
    ('데이터 전처리', 'Sea Ice Drift', 'student2', ['POLARIS-GPU1'], -140, -132),
    ('멀티 GPU 학습 · 2차', 'Language Model', 'student3', ['AURORA-GPU0', 'AURORA-GPU1'], -170, -150),
]

def demo_metrics(resources):
    result = {}
    usages = [92, 0, 67, 0, 78, 0]
    owners = ['student1', None, 'student2', None, 'student2', None]
    for i, resource in enumerate(resources):
        u = usages[i % len(usages)]
        owner = owners[i % len(owners)]
        result[resource['id']] = dict(reachable=True, util=u, util_avg=u, vram_used_mib=round(u * 220),
            vram_total_mib=resource['vram_gb'] * 1024, temperature=round(34 + u * .43), power=round(22 + u * 3.3),
            processes=[dict(user=owner, pid=12480+i, memory_mib=round(u*220))] if owner else [], history_complete=True)
    return result

def demo_report(mapping, days):
    """Deterministic sample aggregates. Labelled as 예시 in the UI; never exported as live metrics."""
    window = days*24
    shares = [(46, .42, 88), (12, .18, 34), (38, .31, 71), (5, .05, 12), (52, .48, 83), (9, .12, 26)]
    gpus = []
    for i, resource in enumerate(mapping['resources']):
        average, reserved_share, busy_share = shares[i % len(shares)]
        busy = round(window*average/100, 1)
        reserved = round(window*reserved_share, 1)
        gpus.append({'id': resource['id'], 'server': resource['server'], 'avg_util': float(average),
                     'busy_hours': busy, 'idle_hours': round(window-busy, 1), 'reserved_hours': reserved,
                     'reserved_busy_percent': float(busy_share) if reserved else None,
                     'coverage_percent': 100.0})
    servers = []
    for index, server in enumerate(mapping['servers']):
        def profile(base, amplitude, offset):
            values = [round(min(99, max(4, base+amplitude*math.sin((hour-offset)/24*2*math.pi))), 1) for hour in range(24)]
            return {'profile': [{'hour': hour, 'value': value} for hour, value in enumerate(values)],
                    'saturated_hours': round(days*sum(1 for v in values if v >= SATURATION_PERCENT), 1),
                    'peak_hour': values.index(max(values)), 'threshold_percent': SATURATION_PERCENT}
        servers.append({**server, 'cpu': profile(52-index*8, 34, 4), 'ram': profile(46-index*6, 28, 6)})
    observed = {'student1': round(window*.31, 1), 'student2': round(window*.22, 1), 'student3': round(window*.08, 1)}
    return {'gpus': gpus, 'servers': servers, 'observed_user_hours': observed}

def demo_history(resources, hours):
    end = now().timestamp()
    return {r['id']: [[end-hours*3600+j*hours*3600/96, round(max(0, min(100, 32+28*math.sin(j/7+i)+20*math.sin(j/3))), 1)]
                      for j in range(97)] for i, r in enumerate(resources)}
