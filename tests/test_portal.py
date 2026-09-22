import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import httpx
import pytest
from fastapi.testclient import TestClient
from portal.app import SESSION_COOKIE, create_app
from portal.alerts import build_alerts
from portal.demo import KST, now
from portal.reports import hourly_profile, summarize_reservations
from portal.integrations import LibreBooking
from portal.integrations import Prometheus
import time
from portal.state import classify

@pytest.fixture
def client(tmp_path):
    with TestClient(create_app('demo',tmp_path/'demo.sqlite3')) as c:
        yield c

def reservation(**changes):
    t=now()+timedelta(days=10)
    return {**dict(title='Test training',project='Research',resources=['S1-GPU2','S1-GPU3'],
                   start=t.isoformat(),end=(t+timedelta(hours=2)).isoformat()),**changes}

def metric(**changes):
    return {**dict(reachable=True,processes=[],vram_used_mib=0,util_avg=0,history_complete=True),**changes}

@pytest.mark.parametrize('overrides,owner,known,expected',[
    ({},None,True,'FREE'),
    ({},{'owner_linux':'a'},True,'RESERVED_IDLE'),
    ({'reachable':False},None,True,'OFFLINE'),
    ({},None,False,'UNKNOWN'),
    ({'processes':None},None,True,'UNKNOWN'),
    ({'history_complete':False},None,True,'UNKNOWN'),
    ({'vram_used_mib':None},None,True,'UNKNOWN'),
    ({'processes':[{'user':'a'}]},None,True,'UNRESERVED_IN_USE'),
    ({'processes':[{'user':'a'}]},{'owner_linux':'a'},True,'RESERVED_IN_USE'),
    ({'processes':[{'user':'b'}]},{'owner_linux':'a'},True,'BORROWED'),
    ({'processes':[{'user':'a'},{'user':'b'}]},{'owner_linux':'a'},True,'CONFLICT'),
    ({'vram_used_mib':2000},{'owner_linux':'a'},True,'UNKNOWN'),
    ({'util_avg':20},None,True,'UNRESERVED_IN_USE'),
])
def test_advisory_states(overrides,owner,known,expected):
    m=metric();m.update(overrides)
    assert classify(m,owner,known)==expected

def test_snapshot_and_history(client):
    result=client.get('/api/snapshot').json()
    assert result['mode']=='demo' and len(result['gpus'])==6
    assert client.get('/api/history?hours=168').status_code==200
    assert client.get('/api/history?hours=2').status_code==422
    assert client.get('/').status_code==200
    assert len(client.get('/api/statistics?hours=168').json()['gpus'])==6
    assert client.get('/api/statistics?hours=1').status_code==422
    assert 'lab_gpu_reserved{' not in client.get('/metrics').text  # Never export demo values as live metrics.

def test_create_edit_delete_and_persistence(client):
    payload=reservation()
    response=client.post('/api/reservations',json=payload)
    assert response.status_code==201,response.text
    row=response.json()
    assert len(row['resources'])==2
    payload['title']='Updated'
    assert client.put('/api/reservations/'+row['id'],json=payload).status_code==200
    assert any(r['title']=='Updated' for r in client.get('/api/snapshot').json()['reservations'])
    with TestClient(create_app('demo',client.app.state.store.path)) as second:
        assert any(r['id']==row['id'] for r in second.get('/api/snapshot').json()['reservations'])
    assert client.delete('/api/reservations/'+row['id']).status_code==204
    assert client.delete('/api/reservations/'+row['id']).status_code==404

def test_atomic_overlap_and_adjacent(client):
    p=reservation()
    assert client.post('/api/reservations',json=p).status_code==201
    p['resources']=['S1-GPU3','S2-GPU1']
    assert client.post('/api/reservations',json=p).status_code==409
    p['resources']=['S2-GPU1']
    assert client.post('/api/reservations',json=p).status_code==201
    p['resources']=['S1-GPU3'];p['start']=p['end'];p['end']=(now()+timedelta(days=10,hours=4)).isoformat()
    assert client.post('/api/reservations',json=p).status_code==201

def test_concurrent_reservations(client):
    p=reservation()
    def request(_):
        return client.post('/api/reservations',json=p).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(request,range(2)))==[201,409]

@pytest.mark.parametrize('field,value', [('resources',['not-a-gpu']),('title','  '),('cpu_cores',-1),('ram_gb',-10),('job_type','Other'),('resources',[])])
def test_bad_fields(client,field,value):
    p=reservation();p[field]=value
    assert client.post('/api/reservations',json=p).status_code==422

def test_dates_and_long_run(client):
    p=reservation();p['end']=p['start']
    assert client.post('/api/reservations',json=p).status_code==422
    p=reservation();p['end']=(now()+timedelta(days=14)).isoformat()
    assert client.post('/api/reservations',json=p).status_code==422
    p['note']='A long experiment'
    assert client.post('/api/reservations',json=p).status_code==201
    p=reservation();p['start']='2030-01-01T00:00:00'
    assert client.post('/api/reservations',json=p).status_code==422

def test_ownership_and_origin(client):
    other=next(r for r in client.get('/api/snapshot').json()['reservations'] if r['owner_linux']=='student2')
    assert client.delete('/api/reservations/'+other['id']).status_code==403
    assert client.put('/api/reservations/'+other['id'],json=reservation()).status_code==403
    assert client.post('/api/reservations',json=reservation(),headers={'Origin':'https://evil.example'}).status_code==403

def test_live_is_read_only_and_outages_are_not_free(tmp_path):
    app=create_app('live',tmp_path/'should-not-exist.sqlite3')
    async def fail(*args):
        raise httpx.ConnectError('offline')
    app.state.lb.reservations=fail
    app.state.prom.snapshot=fail
    with TestClient(app) as c:
        data=c.get('/api/snapshot').json()
        assert len(data['errors'])==2
        assert all(g['state']=='OFFLINE' for g in data['gpus'])
        assert c.post('/api/reservations',json=reservation()).status_code==403
        assert c.delete('/api/reservations/anything').status_code==403
    assert not (tmp_path/'should-not-exist.sqlite3').exists()

def test_librebooking_contract_and_multigpu(monkeypatch):
    mapping={'resources':[{'id':'GPU0','librebooking_resource_id':4},{'id':'GPU1','librebooking_resource_id':5}],
             'users':[{'librebooking_user_id':7,'linux_username':'alice','display_name':'Alice'}]}
    def handler(request):
        if request.url.path.endswith('/Authentication/Authenticate'):
            return httpx.Response(200,json={'isAuthenticated':True,'sessionToken':'secret','userId':7,'sessionExpires':(now()+timedelta(hours=1)).isoformat()})
        assert request.url.path.endswith('/Reservations/')
        assert request.headers['X-Booked-SessionToken']=='secret'
        assert 'startDateTime' in request.url.params
        base={'referenceNumber':'abc','userId':7,'title':'Multi GPU','startDate':now().isoformat(),'endDate':(now()+timedelta(hours=1)).isoformat()}
        return httpx.Response(200,json={'reservations':[{**base,'resourceId':4},{**base,'resourceId':5}]})
    real=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:real(transport=httpx.MockTransport(handler),**kwargs))
    lb=LibreBooking('http://booking/Web/Services/index.php','reader','password',mapping)
    rows=asyncio.run(lb.reservations(now(),now()+timedelta(days=1)))
    assert len(rows)==1 and rows[0]['resources']==['GPU0','GPU1']
    assert rows[0]['owner_linux']=='alice'

@pytest.mark.parametrize('age,up,expected',[(5,1,'FREE'),(120,1,'UNKNOWN'),(5,0,'OFFLINE')])
def test_prometheus_freshness_and_unsupported_values(monkeypatch,age,up,expected):
    stamp=time.time()
    mapping={'resources':[{'id':'S1-GPU0','server':'host1','gpu_index':'0'}],'servers':[{'id':'host1','name':'Host'}]}
    def sample(value,**labels):
        return {'metric':{'server':'host1',**labels},'value':[stamp,str(value)]}
    values={
        'up':[sample(up,job='dcgm-exporter'),sample(1,job='node-exporter')],
        'DCGM_FI_DEV_GPU_UTIL':[sample(0,gpu='0')],
        'avg_over_time(DCGM_FI_DEV_GPU_UTIL[10m])':[sample(0,gpu='0')],
        'count_over_time(DCGM_FI_DEV_GPU_UTIL[10m])':[sample(20,gpu='0')],
        'DCGM_FI_DEV_FB_USED':[sample(10,gpu='0')],
        'DCGM_FI_DEV_FB_FREE':[sample(24566,gpu='0')],
        'DCGM_FI_DEV_POWER_USAGE':[sample(9223372036854775794,gpu='0')],
        'lab_gpu_process_collector_success':[sample(1)],
        'lab_gpu_process_collector_timestamp_seconds':[sample(stamp-age)],
    }
    def handler(request):
        return httpx.Response(200,json={'status':'success','data':{'result':values.get(request.url.params['query'],[])}})
    real=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:real(transport=httpx.MockTransport(handler),**kwargs))
    metrics,servers=asyncio.run(Prometheus('http://prom',mapping).snapshot())
    assert metrics['S1-GPU0']['power'] is None
    assert classify(metrics['S1-GPU0'],None)==expected

def test_live_statistics_metrics_exclude_unknown(tmp_path):
    app=create_app('live',tmp_path/'unused')
    async def reservations(*args):
        return []
    async def metrics():
        return {'S1-GPU0':metric()},[]
    app.state.lb.reservations=reservations
    app.state.prom.snapshot=metrics
    with TestClient(app) as c:
        output=c.get('/metrics').text
    assert 'lab_gpu_reserved{server="gpu-server-1",gpu="0"} 0' in output
    assert 'gpu="1"' not in output

@pytest.fixture
def portal(tmp_path,monkeypatch):
    monkeypatch.setenv('PORTAL_SIGNUP_CODE','LAB-CODE')
    monkeypatch.delenv('PROMETHEUS_URL',raising=False)
    app=create_app('standalone',tmp_path/'portal.sqlite3')
    with TestClient(app) as c:
        yield c

def signup(client,username='kimminsu',password='lab-password-1',display_name='김민수',code='LAB-CODE'):
    return client.post('/api/auth/signup',json={'username':username,'password':password,'display_name':display_name,'code':code})

def test_signup_requires_the_lab_code(portal):
    assert portal.get('/api/session').json()=={'mode':'standalone','authenticated':False,'user':None,
                                               'requires_login':True,'first_account':True,'monitoring':False}
    assert signup(portal,code='wrong').status_code==403
    assert signup(portal,code='').status_code==403
    assert portal.get('/api/snapshot').status_code==401  # Nothing is readable before signing in.

@pytest.mark.parametrize('field,value,status',[('username','ab',422),('username','Kim Min Su',422),
                                               ('password','short',422),('display_name','   ',422)])
def test_signup_validates_fields(portal,field,value,status):
    assert signup(portal,**{field:value}).status_code==status

def test_first_account_is_admin_and_signs_in(portal):
    created=signup(portal).json()
    assert created['role']=='admin' and created['username']=='kimminsu'
    assert portal.cookies.get(SESSION_COOKIE)
    session=portal.get('/api/session').json()
    assert session['authenticated'] and session['user']['display_name']=='김민수' and not session['first_account']
    assert portal.get('/api/snapshot').status_code==200
    assert signup(portal,username='kimminsu',display_name='다른 사람').status_code==409
    second=signup(portal,username='leeseoyeon',display_name='이서연').json()
    assert second['role']=='member'

def test_login_logout_and_throttle(portal):
    signup(portal)
    portal.post('/api/auth/logout')
    assert portal.get('/api/snapshot').status_code==401
    assert portal.post('/api/auth/login',json={'username':'kimminsu','password':'wrong'}).status_code==401
    assert portal.post('/api/auth/login',json={'username':'KimMinSu','password':'lab-password-1'}).status_code==200
    assert portal.get('/api/snapshot').json()['current_user']['username']=='kimminsu'
    portal.post('/api/auth/logout')
    for _ in range(5):
        portal.post('/api/auth/login',json={'username':'kimminsu','password':'wrong'})
    blocked=portal.post('/api/auth/login',json={'username':'kimminsu','password':'lab-password-1'})
    assert blocked.status_code==429 and '초 후' in blocked.json()['detail']

def test_reservations_belong_to_the_signed_in_member(portal):
    signup(portal)
    row=portal.post('/api/reservations',json=reservation()).json()
    assert row['owner_linux']=='kimminsu' and row['owner_name']=='김민수'
    assert portal.get('/api/snapshot').json()['gpus'][2]['reservation'] is None  # Future booking, not current.
    with TestClient(portal.app) as other:
        signup(other,username='leeseoyeon',password='lab-password-2',display_name='이서연')
        assert other.put('/api/reservations/'+row['id'],json=reservation()).status_code==403
        assert other.delete('/api/reservations/'+row['id']).status_code==403
        mine=other.post('/api/reservations',json=reservation(resources=['S2-GPU0'])).json()
        assert mine['owner_name']=='이서연'
    assert portal.delete('/api/reservations/'+mine['id']).status_code==204  # The first account is admin.

def test_standalone_without_monitoring_is_explicit(portal):
    signup(portal)
    data=portal.get('/api/snapshot').json()
    assert data['monitoring'] is False and '모니터링' in data['notice']
    assert all(g['state']=='OFFLINE' for g in data['gpus'])
    assert portal.get('/api/history?hours=24').status_code==503
    assert portal.get('/api/reports?days=7').status_code==503
    assert 'lab_gpu_busy{' not in portal.get('/metrics').text

def test_members_list_hides_roles_from_members(portal):
    signup(portal)
    with TestClient(portal.app) as other:
        signup(other,username='leeseoyeon',password='lab-password-2',display_name='이서연')
        rows=other.get('/api/members').json()['members']
        assert {r['display_name'] for r in rows}=={'김민수','이서연'} and all(r['role'] is None for r in rows)
    assert [r['role'] for r in portal.get('/api/members').json()['members']]==['admin','member']

def gpu(state,**changes):
    return {'id':'S1-GPU0','server':'gpu-server-1','state':state,'metrics':metric(),'reservation':None,**changes}

def test_alerts_cover_overrun_after_reservation_end():
    t=now()
    reservations=[dict(id='r1',resources=['S1-GPU0'],owner_linux='student2',owner_name='이서연',
                       start=(t-timedelta(hours=3)).isoformat(),end=(t-timedelta(minutes=30)).isoformat())]
    running=metric(processes=[{'user':'student2'}],vram_used_mib=9000)
    alerts=build_alerts([gpu('UNRESERVED_IN_USE',metrics=running)],reservations,t)
    assert [a['title'] for a in alerts]==['예약 종료 후 사용 지속']
    assert '이서연' in alerts[0]['message']
    old=[{**reservations[0],'end':(t-timedelta(days=2)).isoformat()}]  # Long past: history, not an overrun.
    assert build_alerts([gpu('UNRESERVED_IN_USE',metrics=running)],old,t)==[]

def test_alerts_priority_and_display_names():
    t=now()
    reservation=dict(id='r2',resources=['S1-GPU0'],owner_linux='student1',owner_name='김민수',
                     start=(t-timedelta(minutes=5)).isoformat(),end=(t+timedelta(hours=2)).isoformat())
    gpus=[gpu('BORROWED',metrics=metric(processes=[{'user':'student2'}]),reservation=reservation),
          gpu('OFFLINE',id='S1-GPU1'),gpu('UNKNOWN',id='S1-GPU2'),gpu('CONFLICT',id='S1-GPU3'),gpu('FREE',id='S2-GPU0')]
    alerts=build_alerts(gpus,[reservation],t,['연동 오류'],[{'linux_username':'student2','display_name':'이서연'}])
    assert [a['level'] for a in alerts]==['danger','danger','warning','warning','info']
    borrowed=next(a for a in alerts if a['resource']=='S1-GPU0')
    assert borrowed['title']=='예약 시간에 다른 사용자 사용 중' and '이서연' in borrowed['message']
    assert not build_alerts([gpu('FREE'),gpu('RESERVED_IDLE',id='S1-GPU1')],[],t)

def test_snapshot_exposes_alerts(client):
    assert isinstance(client.get('/api/snapshot').json()['alerts'],list)

def test_reservation_summary_clips_window_and_counts_multi_gpu():
    end=now()
    start=end-timedelta(days=7)
    rows=[dict(resources=['S1-GPU0','S1-GPU1'],owner_linux='student1',start=(end-timedelta(hours=2)).isoformat(),end=end.isoformat()),
          dict(resources=['S1-GPU0'],owner_linux='student2',start=(start-timedelta(hours=4)).isoformat(),end=(start+timedelta(hours=1)).isoformat()),
          dict(resources=['S1-GPU0'],owner_linux='student2',start=(start-timedelta(days=3)).isoformat(),end=(start-timedelta(days=2)).isoformat())]
    summary=summarize_reservations(rows,start,end,[{'linux_username':'student1','display_name':'김민수'}])
    assert summary['total']==2 and summary['multi_gpu']==1 and summary['multi_gpu_percent']==50.0
    assert summary['reserved_gpu_hours']==5.0  # 2h x 2 GPUs inside the window, plus 1h clipped at the edge.
    assert summary['by_user'][0]=={'user':'student1','name':'김민수','reserved_gpu_hours':4.0,'reservations':1}

def test_hourly_profile_buckets_by_kst_hour():
    base=datetime(2026,9,1,0,0,tzinfo=KST)
    series=[[(base+timedelta(hours=h)).timestamp(),90 if h==3 else 10] for h in range(24)]
    profile=hourly_profile(series,KST)
    assert profile['peak_hour']==3 and profile['saturated_hours']==1
    assert profile['profile'][3]['value']==90 and profile['profile'][0]['value']==10

def test_reports_demo_and_bad_period(client):
    data=client.get('/api/reports?days=30').json()
    assert len(data['gpus'])==6 and len(data['servers'])==2
    assert all(len(s['cpu']['profile'])==24 for s in data['servers'])
    assert data['reservations']['multi_gpu']>=1
    assert all('used_gpu_hours' in u for u in data['users'])
    assert client.get('/api/reports?days=3').status_code==422

def test_reports_failure_is_not_silent(tmp_path):
    app=create_app('live',tmp_path/'unused')
    async def fail(*args):
        raise httpx.ConnectError('offline')
    app.state.lb.reservations=fail
    app.state.prom.report=fail
    with TestClient(app) as c:
        assert c.get('/api/reports?days=7').status_code==503

def test_live_metrics_expose_busy_and_borrowed(tmp_path):
    app=create_app('live',tmp_path/'unused')
    reservation=dict(id='r3',resources=['S1-GPU0'],owner_linux='student1',owner_name='김민수',
                     start=(now()-timedelta(hours=1)).isoformat(),end=(now()+timedelta(hours=1)).isoformat())
    async def reservations(*args):
        return [reservation]
    async def metrics():
        return {'S1-GPU0':metric(processes=[{'user':'student2'}],vram_used_mib=9000)},[]
    app.state.lb.reservations=reservations
    app.state.prom.snapshot=metrics
    with TestClient(app) as c:
        output=c.get('/metrics').text
    labels='{server="gpu-server-1",gpu="0"}'
    assert f'lab_gpu_busy{labels} 1' in output
    assert f'lab_gpu_borrowed{labels} 1' in output
    assert f'lab_gpu_reserved_busy{labels} 1' in output

def test_process_memory_is_joined_by_pid(monkeypatch):
    stamp=time.time()
    mapping={'resources':[{'id':'S1-GPU0','server':'host1','gpu_index':'0'}],'servers':[{'id':'host1','name':'Host'}]}
    def sample(value,**labels):
        return {'metric':{'server':'host1',**labels},'value':[stamp,str(value)]}
    values={
        'up':[sample(1,job='dcgm-exporter'),sample(1,job='node-exporter')],
        'DCGM_FI_DEV_GPU_UTIL':[sample(70,gpu='0')],
        'avg_over_time(DCGM_FI_DEV_GPU_UTIL[10m])':[sample(70,gpu='0')],
        'count_over_time(DCGM_FI_DEV_GPU_UTIL[10m])':[sample(20,gpu='0')],
        'DCGM_FI_DEV_FB_USED':[sample(9000,gpu='0')],
        'lab_gpu_process_info':[sample(1,gpu='0',user='student1',pid='4242')],
        'lab_gpu_process_memory_mib':[sample(8800,gpu='0',user='student1',pid='4242')],
        'lab_gpu_process_collector_success':[sample(1)],
        'lab_gpu_process_collector_timestamp_seconds':[sample(stamp-5)],
    }
    def handler(request):
        return httpx.Response(200,json={'status':'success','data':{'result':values.get(request.url.params['query'],[])}})
    real=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:real(transport=httpx.MockTransport(handler),**kwargs))
    metrics,_=asyncio.run(Prometheus('http://prom',mapping).snapshot())
    assert metrics['S1-GPU0']['processes']==[{'user':'student1','pid':'4242','memory_mib':8800.0}]

def test_prometheus_report_contract(monkeypatch):
    mapping={'resources':[{'id':'S1-GPU0','server':'host1','gpu_index':'0'}],'servers':[{'id':'host1','name':'Host'}]}
    hour=datetime(2026,9,1,15,0,tzinfo=KST).timestamp()
    def instant(value,**labels):
        return [{'metric':{'server':'host1','gpu':'0',**labels},'value':[hour,str(value)]}]
    days=7
    answers={
        f'avg_over_time(DCGM_FI_DEV_GPU_UTIL[{days}d])':instant(41.25),
        f'sum_over_time(lab_gpu_busy[{days}d])':instant(1200),    # 10 h of 30 s samples
        f'count_over_time(lab_gpu_busy[{days}d])':instant(2400),  # 20 h observed
        f'sum_over_time(lab_gpu_reserved[{days}d])':instant(2400),
        f'sum_over_time(lab_gpu_reserved_busy[{days}d])':instant(600),
        f'sum by(user) (count_over_time(lab:gpu_user_active:max[{days}d]))':[{'metric':{'user':'student1'},'value':[hour,'360']}],
    }
    def handler(request):
        if 'start' in request.url.params:
            return httpx.Response(200,json={'status':'success','data':{'result':[
                {'metric':{'server':'host1'},'values':[[hour,'92'],[hour+3600,'12']]}]}})
        return httpx.Response(200,json={'status':'success','data':{'result':answers.get(request.url.params['query'],[])}})
    real=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:real(transport=httpx.MockTransport(handler),**kwargs))
    report=asyncio.run(Prometheus('http://prom',mapping).report(days,KST))
    row=report['gpus'][0]
    assert row['avg_util']==41.2 and row['busy_hours']==10.0 and row['idle_hours']==10.0
    assert row['reserved_hours']==20.0 and row['reserved_busy_percent']==25.0
    assert row['coverage_percent']==round(100*2400/(days*24*120),1)
    assert report['observed_user_hours']=={'student1':3.0}
    assert report['servers'][0]['cpu']['saturated_hours']==1 and report['servers'][0]['cpu']['peak_hour']==15
