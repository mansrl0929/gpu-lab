import asyncio
import logging
import os
import secrets
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
import yaml
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse
from starlette.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator
from .accounts import LoginThrottle, SESSION_DAYS, normalize_username, validate_signup
from .alerts import build_alerts
from .demo import DEMO_MEMBERS, DEMO_SAMPLES, KST, demo_history, demo_metrics, demo_report, now
from .integrations import LibreBooking, Prometheus
from .reports import merge_user_hours, summarize_reservations
from .state import classify
from .store import Store

BUSY_STATES = ('RESERVED_IN_USE','BORROWED','UNRESERVED_IN_USE')
SESSION_COOKIE = 'gpulab_session'
MODES = ('standalone','demo','live')
OPEN_PATHS = ('/api/health','/api/session','/api/auth/')
DEMO_VIEWER = {'id':'demo','username':'student1','display_name':'김민수','role':'member'}

ROOT = Path(__file__).resolve().parent.parent
logger = logging.getLogger('gpu-lab')

class ReservationInput(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    project: str = Field(min_length=1, max_length=120)
    resources: list[str] = Field(min_length=1, max_length=32)
    start: datetime
    end: datetime
    cpu_cores: int = Field(default=0, ge=0, le=4096)
    ram_gb: int = Field(default=0, ge=0, le=65536)
    job_type: Literal['Training','Inference','Preprocess'] = 'Training'
    note: str = Field(default='', max_length=2000)

    @field_validator('title','project')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('필수 항목을 입력하세요.')
        return value.strip()

    @model_validator(mode='after')
    def validate_dates(self):
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError('시간대가 필요합니다.')
        if self.end <= self.start:
            raise ValueError('종료 시각은 시작 시각 이후여야 합니다.')
        if self.end <= now():
            raise ValueError('이미 종료된 시간은 예약할 수 없습니다.')
        if self.end-self.start > timedelta(hours=48) and not self.note.strip():
            raise ValueError('48시간 초과 예약은 장기 사용 사유를 입력하세요.')
        self.resources = list(dict.fromkeys(self.resources))
        return self

class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)

class SignupInput(Credentials):
    display_name: str = Field(min_length=1, max_length=40)
    code: str = Field(default='', max_length=200)

def signup_code():
    """Read the lab signup code from the environment, or from a file kept out of git."""
    code = os.getenv('PORTAL_SIGNUP_CODE','').strip()
    if code:
        return code
    path = Path(os.getenv('PORTAL_SIGNUP_CODE_FILE', str(ROOT/'data/signup_code.txt')))
    if path.exists():
        stored = path.read_text(encoding='utf-8').strip()
        if stored:
            return stored
    generated = secrets.token_urlsafe(6)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(generated+'\n', encoding='utf-8')
    logger.warning('가입 코드를 새로 만들었습니다: %s (파일: %s)', generated, path)
    return generated

def create_app(mode=None, db_path=None, mapping=None):
    mode = mode or os.getenv('PORTAL_MODE','standalone')
    if mode not in MODES:
        raise ValueError(f'PORTAL_MODE must be one of {", ".join(MODES)}')
    mapping = mapping or yaml.safe_load(Path(os.getenv('RESOURCE_MAPPING', str(ROOT/'config/resources.yml'))).read_text(encoding='utf-8'))
    app = FastAPI(title='GPU Lab Portal', docs_url='/api/docs', redoc_url=None)
    store = Store(db_path or os.getenv('PORTAL_DB') or ROOT/('data/demo.sqlite3' if mode == 'demo' else 'data/portal.sqlite3')) if mode != 'live' else None
    if mode == 'demo':
        store.seed_demo(DEMO_SAMPLES, DEMO_MEMBERS)
    code = signup_code() if mode == 'standalone' else None
    throttle = LoginThrottle()
    prometheus_url = os.getenv('PROMETHEUS_URL', '' if mode == 'standalone' else 'http://localhost:9090')
    monitoring = bool(prometheus_url) or mode == 'demo'
    lb = LibreBooking(os.getenv('LIBREBOOKING_API_URL','http://localhost:8080/Web/Services/index.php'),
                     os.getenv('LIBREBOOKING_USERNAME',''), os.getenv('LIBREBOOKING_PASSWORD',''), mapping)
    prom = Prometheus(prometheus_url or 'http://localhost:9090', mapping)
    app.state.lb, app.state.prom, app.state.store, app.state.mode = lb, prom, store, mode
    cache = {'at': 0, 'data': None}
    lock = asyncio.Lock()
    links = {'booking': os.getenv('LIBREBOOKING_PUBLIC_URL','http://localhost:8080/Web'),
             'grafana': os.getenv('GRAFANA_PUBLIC_URL','http://localhost:3001'),
             'homepage': os.getenv('HOMEPAGE_PUBLIC_URL','http://localhost:3000')}
    if any(urlparse(url).scheme not in ('http','https') for url in links.values()):
        raise ValueError('Service URLs must use http or https')
    retention = int(os.getenv('PROMETHEUS_RETENTION_DAYS','30'))
    cache_seconds = float(os.getenv('SNAPSHOT_CACHE_SECONDS','4'))
    secure_cookie = os.getenv('PORTAL_SECURE_COOKIES','').lower() in ('1','true','yes')
    user_map = {u.get('portal_username') or u.get('linux_username'): u for u in mapping.get('users',[]) if u.get('linux_username')}

    def session_user(request: Request):
        return store.resolve_session(request.cookies.get(SESSION_COOKIE)) if store else None

    def require_user(request: Request):
        user = session_user(request)
        if mode == 'standalone' and not user:
            raise HTTPException(401,'로그인이 필요합니다.')
        # Demo mode is an anonymous preview; reservations then belong to the sample account.
        return user or (DEMO_VIEWER if mode == 'demo' else None)

    def start_session(response: Response, user):
        response.set_cookie(SESSION_COOKIE, store.start_session(user['id']), max_age=SESSION_DAYS*86400,
                            httponly=True, samesite='lax', secure=secure_cookie, path='/')

    def linux_name(username):
        # Members sign up with a portal id only; admins may map it to a different Linux account.
        return (user_map.get(username) or {}).get('linux_username', username)

    @app.middleware('http')
    async def headers(request: Request, call_next):
        path = request.url.path
        if request.method in ('POST','PUT','DELETE'):
            origin = request.headers.get('origin')
            if origin and urlparse(origin).netloc != request.headers.get('host'):
                return JSONResponse({'detail':'Cross-origin request rejected'},403)
        if mode == 'standalone' and path.startswith('/api/') and not path.startswith(OPEN_PATHS):
            if not store.resolve_session(request.cookies.get(SESSION_COOKIE)):
                return JSONResponse({'detail':'로그인이 필요합니다.'},401)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; script-src 'self'; frame-ancestors 'self'; base-uri 'self'; form-action 'self'"
        if path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/api/health')
    def health():
        return {'status':'ok','mode':mode}

    # --- accounts -----------------------------------------------------------

    @app.get('/api/session')
    def session(request: Request):
        user = session_user(request)
        return {'mode':mode,'authenticated':bool(user) or mode != 'standalone','user':user,
                'requires_login':mode == 'standalone','first_account':bool(store and store.count_users() == 0),'monitoring':monitoring}

    @app.post('/api/auth/signup',status_code=201)
    def signup(data: SignupInput, response: Response):
        if mode != 'standalone':
            raise HTTPException(403,'이 모드에서는 계정을 만들지 않습니다.')
        if not secrets.compare_digest(data.code.strip().encode('utf-8'), code.encode('utf-8')):
            raise HTTPException(403,'가입 코드가 올바르지 않습니다. 연구실 관리자에게 확인하세요.')
        message = validate_signup(data.username,data.password,data.display_name)
        if message:
            raise HTTPException(422,message)
        try:
            user = store.create_user(data.username,data.password,data.display_name)
        except ValueError as exc:
            raise HTTPException(409,str(exc)) from exc
        start_session(response,user)
        logger.info('New account: %s (%s)',user['username'],user['role'])
        return user

    @app.post('/api/auth/login')
    def login(data: Credentials, request: Request, response: Response):
        if mode != 'standalone':
            raise HTTPException(403,'이 모드에서는 로그인을 사용하지 않습니다.')
        key = f'{request.client.host if request.client else "?"}|{normalize_username(data.username)}'
        wait = throttle.locked_for(key)
        if wait:
            raise HTTPException(429,f'로그인 시도가 많습니다. {wait}초 후 다시 시도하세요.')
        user = store.authenticate(data.username,data.password)
        if not user:
            throttle.record_failure(key)
            raise HTTPException(401,'아이디 또는 비밀번호가 올바르지 않습니다.')
        throttle.reset(key)
        start_session(response,user)
        return user

    @app.post('/api/auth/logout',status_code=204)
    def logout(request: Request, response: Response):
        if store:
            store.end_session(request.cookies.get(SESSION_COOKIE))
        response.delete_cookie(SESSION_COOKIE,path='/')

    @app.get('/api/members')
    def members(user=Depends(require_user)):
        if not store:
            return {'members':[]}
        rows = store.list_users()
        return {'members':[{'username':r['username'],'display_name':r['display_name'],
                            'role':r['role'] if user and user['role'] == 'admin' else None,
                            'linux_username':linux_name(r['username'])} for r in rows]}

    # --- status -------------------------------------------------------------

    @app.get('/api/snapshot')
    async def snapshot(request: Request=None):
        async with lock:
            if mode != 'demo' and cache['data'] and time.monotonic()-cache['at'] < cache_seconds:
                viewer = session_user(request) if request is not None else None
                return {**cache['data'],'current_user':viewer}
            timestamp = now()
            errors, notice = [], None
            known = True
            if mode == 'demo':
                reservations, metrics = store.list(), demo_metrics(mapping['resources'])
                servers = [{**s,'online':True,'cpu':[62,38][i%2],'ram':[58,34][i%2],'ram_total':128,'disk':[42,61][i%2]} for i,s in enumerate(mapping['servers'])]
            elif mode == 'standalone':
                reservations = store.list()
                if monitoring:
                    try:
                        metrics, servers = await prom.snapshot()
                    except Exception as exc:
                        logger.warning('Prometheus unavailable: %s',type(exc).__name__)
                        errors.append('모니터링 연결이 끊겼습니다. 실제 사용 상태를 확인하세요.')
                        metrics, servers = {}, [{**s,'online':False,'cpu':None,'ram':None,'ram_total':None,'disk':None} for s in mapping['servers']]
                else:
                    notice = '모니터링(Prometheus)이 아직 연결되지 않았습니다. 예약과 일정은 정상 동작하며, GPU 실시간 상태는 연동 후 표시됩니다.'
                    metrics, servers = {}, [{**s,'online':None,'cpu':None,'ram':None,'ram_total':None,'disk':None} for s in mapping['servers']]
            else:
                start = timestamp.replace(hour=0,minute=0,second=0,microsecond=0)-timedelta(days=7)
                lb_result, prom_result = await asyncio.gather(lb.reservations(start,timestamp+timedelta(days=30)),prom.snapshot(),return_exceptions=True)
                known = not isinstance(lb_result,Exception)
                reservations = lb_result if known else []
                if not known:
                    logger.warning('LibreBooking unavailable: %s',type(lb_result).__name__)
                    errors.append('예약 정보를 가져오지 못했습니다. 예약 여부를 확인할 수 없습니다.')
                if isinstance(prom_result,Exception):
                    logger.warning('Prometheus unavailable: %s',type(prom_result).__name__)
                    errors.append('모니터링 연결이 끊겼습니다. 실제 사용 상태를 확인하세요.')
                    metrics = {}
                    servers = [{**s,'online':False,'cpu':None,'ram':None,'ram_total':None,'disk':None} for s in mapping['servers']]
                else:
                    metrics, servers = prom_result
            matched = reservations if mode == 'live' else [{**r,'owner_linux':linux_name(r['owner_linux'])} for r in reservations]
            gpus = []
            for resource in mapping['resources']:
                current = [r for r in matched if resource['id'] in r['resources'] and datetime.fromisoformat(r['start']) <= timestamp < datetime.fromisoformat(r['end'])]
                metric = metrics.get(resource['id'], {'reachable':False})
                reservation = current[0] if current else None
                state = classify(metric,reservation,known)
                if len(current)>1 and metric.get('reachable'):
                    state='CONFLICT'
                gpus.append({**resource, 'metrics':metric,'reservation':reservation,'state':state})
            data = dict(mode=mode, updated_at=timestamp.isoformat(), timezone='Asia/Seoul', servers=servers,gpus=gpus,
                        reservations=sorted(reservations,key=lambda r:r['start']), errors=errors, notice=notice,
                        monitoring=monitoring, links=links,
                        alerts=build_alerts(gpus,matched,timestamp,errors,mapping.get('users',[])))
            cache.update(at=time.monotonic(),data=data)
            viewer = session_user(request) if request is not None else None
            return {**data,'current_user':viewer or (DEMO_VIEWER if mode == 'demo' else None)}

    def monitoring_required():
        if not monitoring:
            raise HTTPException(503,'모니터링이 아직 연결되지 않았습니다. PROMETHEUS_URL을 설정하면 사용률과 리포트가 표시됩니다.')

    @app.get('/api/history')
    async def history(hours: int=24):
        if hours not in (24,168):
            raise HTTPException(422,'조회 기간은 24시간 또는 168시간입니다.')
        monitoring_required()
        try:
            series = demo_history(mapping['resources'],hours) if mode=='demo' else await prom.history(hours)
            return {'mode':mode,'hours':hours,'series':series}
        except Exception as exc:
            logger.warning('History unavailable: %s',type(exc).__name__)
            raise HTTPException(503,'사용률 이력을 가져오지 못했습니다.') from exc

    @app.get('/metrics',response_class=PlainTextResponse)
    async def metrics_endpoint():
        # Prometheus persists advisory observations from now onward, without a second database.
        data = await snapshot()
        lines = ['# TYPE lab_gpu_reserved gauge','# TYPE lab_gpu_reserved_busy gauge',
                 '# TYPE lab_gpu_busy gauge','# TYPE lab_gpu_borrowed gauge']
        if mode != 'demo' and monitoring and not data['errors']:
            for g in data['gpus']:
                if g['state'] in ('UNKNOWN','OFFLINE'):
                    continue
                label = lambda s: str(s).replace('\\','\\\\').replace('\n','\\n').replace('"','\\"')
                labels = f'server="{label(g["server"])}",gpu="{label(g["gpu_index"])}"'
                reserved = int(g['reservation'] is not None)
                # A double-booked but idle GPU is CONFLICT without being busy, so read the processes.
                busy = int(g['state'] in BUSY_STATES or (g['state']=='CONFLICT' and bool(g['metrics'].get('processes'))))
                borrowed = int(g['state'] in ('BORROWED','CONFLICT'))
                lines += [f'lab_gpu_reserved{{{labels}}} {reserved}',f'lab_gpu_reserved_busy{{{labels}}} {reserved*busy}',
                          f'lab_gpu_busy{{{labels}}} {busy}',f'lab_gpu_borrowed{{{labels}}} {borrowed}']
        return '\n'.join(lines)+'\n'

    @app.get('/api/statistics')
    async def statistics(hours: int=24):
        if hours not in (24,168):
            raise HTTPException(422,'조회 기간은 24시간 또는 168시간입니다.')
        if mode=='demo':
            return {'mode':mode,'hours':hours,'gpus':[{ 'id':r['id'],'reserved_hours':[8,6,0,0,9,3][i%6],
                'used_percent':[81,24,None,None,75,46][i%6],'coverage_percent':100} for i,r in enumerate(mapping['resources'])]}
        monitoring_required()
        try:
            rows = await prom.statistics(hours)
            return {'mode':mode,'hours':hours,'gpus':rows}
        except Exception as exc:
            raise HTTPException(503,'예약 대비 사용 통계를 가져오지 못했습니다.') from exc

    @app.get('/api/reports')
    async def reports(days: int=7):
        # Sizing and planning aggregates only; never a per-student evaluation.
        if days not in (7,30):
            raise HTTPException(422,'리포트 기간은 7일 또는 30일입니다.')
        timestamp = now()
        start = timestamp-timedelta(days=days)
        users = mapping.get('users',[])
        if mode == 'demo':
            rows, report = store.list(), demo_report(mapping,days)
        elif mode == 'standalone':
            monitoring_required()
            rows = store.list()
            try:
                report = await prom.report(days,KST)
            except Exception as exc:
                logger.warning('Report unavailable: %s',type(exc).__name__)
                raise HTTPException(503,'운영 리포트를 가져오지 못했습니다.') from exc
        else:
            try:
                rows, report = await asyncio.gather(lb.reservations(start,timestamp),prom.report(days,KST))
            except Exception as exc:
                logger.warning('Report unavailable: %s',type(exc).__name__)
                raise HTTPException(503,'운영 리포트를 가져오지 못했습니다.') from exc
        summary = summarize_reservations(rows,start,timestamp,users)
        return {'mode':mode,'days':days,'generated_at':timestamp.isoformat(),'retention_days':retention,
                'gpus':report['gpus'],'servers':report['servers'],'reservations':summary,
                'users':merge_user_hours(summary['by_user'],report['observed_user_hours'],users)}

    # --- reservations -------------------------------------------------------

    def writable():
        if mode == 'live':
            raise HTTPException(403,'실제 예약 생성·수정·취소는 LibreBooking에서 진행하세요.')

    def save(data, user, reservation_id=None):
        writable()
        if not set(data.resources) <= {r['id'] for r in mapping['resources']}:
            raise HTTPException(422,'등록되지 않은 GPU입니다.')
        try:
            saved = store.save(data.model_dump(mode='json'),user,reservation_id)
            cache['at'] = 0
            return saved
        except (PermissionError,LookupError,ValueError) as exc:
            raise HTTPException(403 if isinstance(exc,PermissionError) else 404 if isinstance(exc,LookupError) else 409,str(exc)) from exc

    @app.post('/api/reservations',status_code=201)
    def create(data: ReservationInput, user=Depends(require_user)):
        return save(data,user)

    @app.put('/api/reservations/{reservation_id}')
    def update(reservation_id: str, data: ReservationInput, user=Depends(require_user)):
        return save(data,user,reservation_id)

    @app.delete('/api/reservations/{reservation_id}',status_code=204)
    def delete(reservation_id: str, user=Depends(require_user)):
        writable()
        try:
            store.delete(reservation_id,user)
            cache['at'] = 0
        except (LookupError,PermissionError) as exc:
            raise HTTPException(403 if isinstance(exc,PermissionError) else 404,str(exc)) from exc

    app.mount('/static',StaticFiles(directory=ROOT/'portal/static'),name='static')

    @app.get('/')
    def index():
        return FileResponse(ROOT/'portal/static/index.html')
    return app

app = create_app()
