import asyncio
import math
import time
from datetime import datetime, timedelta
import httpx
from .reports import hourly_profile

INTERVAL_SECONDS = 30  # Prometheus scrape interval; one sample covers this many seconds.

def to_hours(samples):
    return None if samples is None else round(samples*INTERVAL_SECONDS/3600, 1)

class LibreBooking:
    def __init__(self, url, username, password, mapping):
        self.url, self.username, self.password, self.mapping = url.rstrip('/'), username, password, mapping
        self.headers = None
        self.expires = 0

    async def reservations(self, start, end):
        async with httpx.AsyncClient(timeout=12) as client:
            if not self.headers or time.time() >= self.expires:
                response = await client.post(self.url + '/Authentication/Authenticate', json={'username': self.username, 'password': self.password})
                response.raise_for_status()
                auth = response.json()
                if not auth.get('isAuthenticated'):
                    raise ValueError('LibreBooking authentication failed')
                self.headers = {'X-Booked-SessionToken': auth['sessionToken'], 'X-Booked-UserId': str(auth['userId'])}
                self.expires = min(time.time()+300, datetime.fromisoformat(auth['sessionExpires']).timestamp()-10)
            response = await client.get(self.url + '/Reservations/', headers=self.headers,
                params={'startDateTime': start.isoformat(), 'endDateTime': end.isoformat()})
            if response.status_code in (401, 403):
                self.headers = None
            response.raise_for_status()
            payload = response.json()
            rows = payload['reservations']
        resources = {str(r['librebooking_resource_id']): r['id'] for r in self.mapping['resources']}
        users = {str(u['librebooking_user_id']): u for u in self.mapping.get('users', [])}
        grouped = {}
        for row in rows:
            resource = resources.get(str(row['resourceId']))
            if not resource or row.get('requiresApproval'):
                continue
            key = row['referenceNumber']
            user = users.get(str(row['userId']), {})
            if key not in grouped:
                grouped[key] = dict(id=key, title=row['title'], project=row.get('description', ''),
                    start=row['startDate'], end=row['endDate'], owner_linux=user.get('linux_username'),
                    owner_name=user.get('display_name') or (row.get('firstName','')+' '+row.get('lastName','')).strip(),
                    resources=[], cpu_cores=None, ram_gb=None, job_type='', note='', contact=user.get('linux_username',''))
            grouped[key]['resources'].append(resource)
        return list(grouped.values())

class Prometheus:
    def __init__(self, url, mapping):
        self.url, self.mapping = url.rstrip('/'), mapping

    async def query(self, client, expression, **params):
        endpoint = '/api/v1/query_range' if 'start' in params else '/api/v1/query'
        response = await client.get(self.url + endpoint, params={'query': expression, **params})
        response.raise_for_status()
        body = response.json()
        if body.get('status') != 'success':
            raise ValueError('Prometheus query failed')
        return body['data']['result']

    async def snapshot(self):
        queries = {
            'up': 'up', 'util': 'DCGM_FI_DEV_GPU_UTIL',
            'util_avg': 'avg_over_time(DCGM_FI_DEV_GPU_UTIL[10m])',
            'samples': 'count_over_time(DCGM_FI_DEV_GPU_UTIL[10m])',
            'vram_used_mib': 'DCGM_FI_DEV_FB_USED', 'vram_free_mib': 'DCGM_FI_DEV_FB_FREE',
            'temperature': 'DCGM_FI_DEV_GPU_TEMP', 'power': 'DCGM_FI_DEV_POWER_USAGE',
            'processes': 'lab_gpu_process_info', 'process_memory': 'lab_gpu_process_memory_mib',
            'process_ok': 'lab_gpu_process_collector_success',
            'process_time': 'lab_gpu_process_collector_timestamp_seconds',
            'cpu': '100 - (avg by(server) (rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)',
            'ram': '100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)',
            'ram_total': 'node_memory_MemTotal_bytes / 1073741824',
            'disk': '100 * (1 - node_filesystem_avail_bytes{fstype!~"tmpfs|overlay|squashfs",mountpoint=~"/|/data|/scratch"} / node_filesystem_size_bytes{fstype!~"tmpfs|overlay|squashfs",mountpoint=~"/|/data|/scratch"})',
        }
        async with httpx.AsyncClient(timeout=12) as client:
            results = await asyncio.gather(*(self.query(client, q) for q in queries.values()))
        series = dict(zip(queries, results))
        def matches(key, server, gpu=None, job=None):
            return [s for s in series[key] if s['metric'].get('server') == server
                    and (gpu is None or s['metric'].get('gpu') == str(gpu))
                    and (job is None or s['metric'].get('job') == job)]
        def number(key, server, gpu=None, job=None):
            rows = matches(key, server, gpu, job)
            if not rows:
                return None
            stamp, raw = rows[0]['value']
            value = float(raw)
            # DCGM uses large sentinel values for unsupported counters.
            return value if math.isfinite(value) and abs(value) < 1e12 and time.time()-float(stamp) < 120 else None
        metrics = {}
        for r in self.mapping['resources']:
            server, gpu = r['server'], r['gpu_index']
            m = {key: number(key, server, gpu) for key in ['util','util_avg','vram_used_mib','temperature','power']}
            free = number('vram_free_mib', server, gpu)
            m['vram_total_mib'] = m['vram_used_mib']+free if free is not None and m['vram_used_mib'] is not None else None
            # GPU numbers may come from node_exporter's textfile collector or from DCGM Exporter;
            # either source counts as reachable as long as one of them is up and the sample is fresh.
            exporter_up = 1 in (number('up',server,job='node-exporter'), number('up',server,job='dcgm-exporter'))
            m['reachable'] = exporter_up and m['util'] is not None
            m['history_complete'] = (number('samples',server,gpu) or 0) >= 18
            stamp = number('process_time',server)
            process_valid = number('up',server,job='node-exporter') == 1 and number('process_ok',server) == 1 and stamp is not None and 0 <= time.time()-stamp < 90
            used = {p['metric'].get('pid'): float(p['value'][1]) for p in matches('process_memory',server,gpu)
                    if math.isfinite(float(p['value'][1])) and float(p['value'][1]) >= 0}
            m['processes'] = [dict(user=p['metric'].get('user','unknown'),pid=p['metric'].get('pid'),
                                   memory_mib=used.get(p['metric'].get('pid')))
                              for p in matches('processes',server,gpu) if float(p['value'][1]) == 1] if process_valid else None
            metrics[r['id']] = m
        servers = []
        for server in self.mapping['servers']:
            sid = server['id']
            disks = [float(v['value'][1]) for v in matches('disk',sid) if math.isfinite(float(v['value'][1]))]
            servers.append({**server, 'online': number('up',sid,job='node-exporter') == 1,
                            'cpu': number('cpu',sid), 'ram': number('ram',sid), 'ram_total': number('ram_total',sid), 'disk': max(disks) if disks else None})
        return metrics, servers

    async def history(self, hours):
        end = time.time()
        async with httpx.AsyncClient(timeout=15) as client:
            series = await self.query(client, 'DCGM_FI_DEV_GPU_UTIL', start=end-hours*3600, end=end, step=max(300, hours*3600//144))
        output = {}
        for r in self.mapping['resources']:
            row = next((s for s in series if s['metric'].get('server') == r['server'] and s['metric'].get('gpu') == r['gpu_index']), None)
            output[r['id']] = [[t,float(v)] for t,v in row['values'] if math.isfinite(float(v)) and 0 <= float(v) <= 100] if row else []
        return output

    async def report(self, days, tzinfo):
        """Long-window aggregates. lab_gpu_* series start when the portal is first scraped."""
        window = f'[{days}d]'
        instant = {
            'avg_util': f'avg_over_time(DCGM_FI_DEV_GPU_UTIL{window})',
            'busy_samples': f'sum_over_time(lab_gpu_busy{window})',
            'known_samples': f'count_over_time(lab_gpu_busy{window})',
            'reserved_samples': f'sum_over_time(lab_gpu_reserved{window})',
            'reserved_busy_samples': f'sum_over_time(lab_gpu_reserved_busy{window})',
        }
        # The recording rule collapses per-process series, so one sample is one busy GPU per user.
        users = f'sum by(user) (count_over_time(lab:gpu_user_active:max{window}))'
        cpu = 'avg by(server) (100 - (avg by(server,instance) (rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100))'
        ram = 'avg by(server) (100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes))'
        end = time.time()
        start, step = end - days*86400, 3600
        async with httpx.AsyncClient(timeout=25) as client:
            values, user_rows, cpu_rows, ram_rows = await asyncio.gather(
                asyncio.gather(*(self.query(client, q) for q in instant.values())),
                self.query(client, users),
                self.query(client, cpu, start=start, end=end, step=step),
                self.query(client, ram, start=start, end=end, step=step))
        series = dict(zip(instant, values))
        def value(key, server, gpu):
            found = next((s for s in series[key] if s['metric'].get('server') == server and s['metric'].get('gpu') == str(gpu)), None)
            if not found:
                return None
            number = float(found['value'][1])
            return number if math.isfinite(number) else None
        def ratio(numerator, denominator):
            return round(100*numerator/denominator, 1) if numerator is not None and denominator else None
        expected = days*24*3600/INTERVAL_SECONDS
        gpus = []
        for resource in self.mapping['resources']:
            server, gpu = resource['server'], resource['gpu_index']
            known, busy = value('known_samples', server, gpu), value('busy_samples', server, gpu)
            reserved, reserved_busy = value('reserved_samples', server, gpu), value('reserved_busy_samples', server, gpu)
            average = value('avg_util', server, gpu)
            gpus.append({'id': resource['id'], 'server': server,
                         'avg_util': round(average, 1) if average is not None else None,
                         'busy_hours': to_hours(busy), 'idle_hours': to_hours(None if known is None or busy is None else known-busy),
                         'reserved_hours': to_hours(reserved), 'reserved_busy_percent': ratio(reserved_busy, reserved),
                         'coverage_percent': min(100.0, ratio(known, expected)) if known is not None else 0.0})
        def points(rows, server):
            found = next((s for s in rows if s['metric'].get('server') == server), None)
            return [[float(t), float(v)] for t, v in found['values'] if math.isfinite(float(v))] if found else []
        servers = [{**s, 'cpu': hourly_profile(points(cpu_rows, s['id']), tzinfo),
                    'ram': hourly_profile(points(ram_rows, s['id']), tzinfo)} for s in self.mapping['servers']]
        observed = {row['metric'].get('user', 'unknown'): to_hours(float(row['value'][1]))
                    for row in user_rows if math.isfinite(float(row['value'][1]))}
        return {'gpus': gpus, 'servers': servers, 'observed_user_hours': observed}

    async def statistics(self,hours):
        window=f'[{hours}h]'
        queries = {
            'reserved_hours':f'sum_over_time(lab_gpu_reserved{window}) * %d / 3600'%INTERVAL_SECONDS,
            'used_percent':f'100 * sum_over_time(lab_gpu_reserved_busy{window}) / sum_over_time(lab_gpu_reserved{window})',
            'coverage_percent':f'100 * count_over_time(lab_gpu_reserved{window}) / {hours*3600/INTERVAL_SECONDS}',
        }
        async with httpx.AsyncClient(timeout=12) as client:
            results=await asyncio.gather(*(self.query(client,q) for q in queries.values()))
        rows=[]
        for r in self.mapping['resources']:
            row={'id':r['id']}
            for key,series in zip(queries,results):
                found=next((s for s in series if s['metric'].get('server')==r['server'] and s['metric'].get('gpu')==r['gpu_index']),None)
                value=float(found['value'][1]) if found else None
                row[key]=value if value is not None and math.isfinite(value) else None
            rows.append(row)
        return rows
