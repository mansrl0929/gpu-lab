"""Browser acceptance check.

Start the demo server first (python -m uvicorn portal.app:app --port 8000 with PORTAL_MODE=demo);
this script starts its own standalone server on port 8101 for the account flow.
"""
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime,timedelta,timezone
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parent.parent
ARTIFACTS=ROOT/'artifacts'
ARTIFACTS.mkdir(exist_ok=True)
STANDALONE='http://127.0.0.1:8101'
SIGNUP_CODE='BROWSER-CHECK-CODE'

def start_standalone():
    database=Path(tempfile.mkdtemp(prefix='gpu-lab-check'))/'portal.sqlite3'
    environment={**os.environ,'PORTAL_MODE':'standalone','PORTAL_SIGNUP_CODE':SIGNUP_CODE,
                 'PORTAL_DB':str(database),'PROMETHEUS_URL':''}
    process=subprocess.Popen([sys.executable,'-m','uvicorn','portal.app:app','--host','127.0.0.1','--port','8101'],
                             cwd=str(ROOT),env=environment,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            urllib.request.urlopen(STANDALONE+'/api/health',timeout=1)
            return process
        except Exception:
            time.sleep(0.5)
    process.terminate()
    raise SystemExit('standalone server did not start')

standalone=start_standalone()
try:
  with sync_playwright() as p:
     browser=p.chromium.launch()
     page=browser.new_page(viewport={'width':1440,'height':1150},device_scale_factor=1)
     errors=[]
     page.on('pageerror',lambda error:errors.append(str(error)))
     page.goto('http://127.0.0.1:8000')
     page.wait_for_selector('.gpu-card')
     page.wait_for_selector('.chart svg')
     assert page.locator('.gpu-card').count()==6
     page.screenshot(path=str(ARTIFACTS/'dashboard-desktop.png'),full_page=True)
     page.select_option('#server-filter','igdsl-polaris')
     assert page.locator('.gpu-card').count()==2
     page.select_option('#server-filter','all')
     page.fill('#gpu-search','AURORA-GPU3')
     assert page.locator('.gpu-card').count()==1
     page.fill('#gpu-search','')
     page.click('#new-reservation')
     page.fill('[name=title]','Browser acceptance experiment')
     page.fill('[name=project]','UI validation')
     page.check('[name=resources][value=AURORA-GPU2]')
     page.check('[name=resources][value=AURORA-GPU3]')
     start=datetime.now(timezone(timedelta(hours=9)))+timedelta(days=15)
     page.fill('[name=start]',start.strftime('%Y-%m-%dT%H:%M'))
     page.fill('[name=end]',(start+timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M'))
     page.click('#reservation-form button[type=submit]')
     page.wait_for_selector('#reservation-dialog',state='hidden')
     page.click('nav [data-page=mine]')
     row=page.locator('tr').filter(has_text='Browser acceptance experiment')
     row.get_by_role('button',name='상세 보기').click()
     page.click('[data-edit]')
     page.fill('[name=title]','Browser acceptance updated')
     page.click('#reservation-form button[type=submit]')
     page.wait_for_selector('#reservation-dialog',state='hidden')
     page.reload()
     page.wait_for_selector('table')
     row=page.locator('tr').filter(has_text='Browser acceptance updated')
     assert row.count()==1
     row.get_by_role('button',name='상세 보기').click()
     page.click('[data-cancel]')
     page.click('[data-cancel]')
     page.wait_for_selector('#detail-dialog',state='hidden')
     assert page.locator('tr').filter(has_text='Browser acceptance updated').count()==0
     page.click('nav [data-page=reservations]')
     page.wait_for_selector('.calendar')
     page.screenshot(path=str(ARTIFACTS/'calendar-desktop.png'),full_page=True)
     for name in ['monitoring','reports','guide','connections','overview']:
         page.goto('http://127.0.0.1:8000/#'+name)
         page.wait_for_timeout(300)
         assert page.locator('#page-title').inner_text()
         if name=='monitoring':
             page.get_by_role('button',name='최근 7일').click()
             page.wait_for_selector('.table-scroll table tbody tr')
             assert page.get_by_role('heading',name='예약 대비 실사용',exact=False).count()==1
             page.screenshot(path=str(ARTIFACTS/'monitoring-desktop.png'),full_page=True)
         if name=='reports':
             page.wait_for_selector('.hour-strip')
             assert page.locator('.hour-cell').count()==4*24  # CPU and RAM per workstation
             assert page.locator('.table-scroll table tbody tr').count()==6
             page.screenshot(path=str(ARTIFACTS/'reports-desktop.png'),full_page=True)
             page.get_by_role('button',name='최근 30일').click()
             page.wait_for_selector('button[data-report-days="30"].selected')
             page.wait_for_selector('.hour-strip')
             assert '30일' in page.locator('.panel').first.inner_text()
     page.click('#notification-button')
     page.wait_for_selector('#detail-dialog[open]')
     assert page.locator('.alert-item').count()>=1, '데모 상태 알림이 비어 있습니다'
     page.screenshot(path=str(ARTIFACTS/'alerts-desktop.png'))
     page.keyboard.press('Escape')
     page.click('[data-gpu=AURORA-GPU0]')
     page.wait_for_selector('#detail-dialog[open]')
     page.keyboard.press('Escape')
     page.set_viewport_size({'width':390,'height':844})
     page.screenshot(path=str(ARTIFACTS/'dashboard-mobile.png'),full_page=True)
     assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Mobile overflow'
     page.goto('http://127.0.0.1:8000/#reports')
     page.wait_for_selector('.hour-strip')
     page.screenshot(path=str(ARTIFACTS/'reports-mobile.png'),full_page=True)
     assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Mobile overflow on reports'
     page.goto('http://127.0.0.1:8000/#overview')
     page.wait_for_selector('.gpu-card')
     page.click('#new-reservation')
     page.screenshot(path=str(ARTIFACTS/'reservation-mobile.png'),full_page=True)
     page.keyboard.press('Escape')

     # Account flow on a separate standalone workspace.
     account=browser.new_page(viewport={'width':1440,'height':1000})
     account.on('pageerror',lambda error:errors.append(str(error)))
     account.goto(STANDALONE)
     account.wait_for_selector('#auth-screen:not([hidden])')
     assert account.locator('#auth-title').inner_text()=='첫 관리자 계정 만들기'
     account.screenshot(path=str(ARTIFACTS/'signup-desktop.png'))
     account.fill('#signup-form [name=username]','labadmin')
     account.fill('#signup-form [name=display_name]','연구실 관리자')
     account.fill('#signup-form [name=password]','browser-check-pass')
     account.fill('#signup-form [name=code]','wrong-code')
     account.click('#signup-form button[type=submit]')
     account.wait_for_selector('#signup-error:not(:empty)')
     assert '가입 코드' in account.locator('#signup-error').inner_text()
     account.fill('#signup-form [name=code]',SIGNUP_CODE)
     account.click('#signup-form button[type=submit]')
     account.wait_for_selector('.gpu-card')
     assert '관리자' in account.locator('#profile-name').inner_text()
     assert '모니터링' in account.locator('#demo-banner').inner_text()
     account.screenshot(path=str(ARTIFACTS/'standalone-desktop.png'),full_page=True)
     account.click('#new-reservation')
     account.fill('[name=title]','계정 예약 검증')
     account.fill('[name=project]','Account flow')
     account.check('[name=resources][value=AURORA-GPU0]')
     start=datetime.now(timezone(timedelta(hours=9)))+timedelta(days=3)
     account.fill('[name=start]',start.strftime('%Y-%m-%dT%H:%M'))
     account.fill('[name=end]',(start+timedelta(hours=3)).strftime('%Y-%m-%dT%H:%M'))
     account.click('#reservation-form button[type=submit]')
     account.wait_for_selector('#reservation-dialog',state='hidden')
     account.click('nav [data-page=mine]')
     account.wait_for_selector('table')
     assert account.locator('tr').filter(has_text='계정 예약 검증').count()==1
     account.click('#logout-button')
     account.wait_for_selector('#auth-screen:not([hidden])')
     account.fill('#login-form [name=username]','labadmin')
     account.fill('#login-form [name=password]','wrong-password')
     account.click('#login-form button[type=submit]')
     account.wait_for_selector('#login-error:not(:empty)')
     account.fill('#login-form [name=password]','browser-check-pass')
     account.click('#login-form button[type=submit]')
     account.wait_for_selector('#auth-screen',state='hidden')  # Signing in restores the page that was open.
     account.goto(STANDALONE+'/#mine')
     account.wait_for_selector('table')
     assert account.locator('tr').filter(has_text='계정 예약 검증').count()==1, '로그인 후 예약이 유지되지 않았습니다'
     account.set_viewport_size({'width':390,'height':844})
     account.goto(STANDALONE)
     account.wait_for_selector('.gpu-card')
     assert account.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Mobile overflow on standalone'

     assert not errors,errors
     browser.close()
finally:
  standalone.terminate()
print('PASS: signup with lab code, login/logout, per-account reservations, navigation, GPU filters, multi-GPU create/edit/persistence/cancel, operating report (7/30 days), alerts, desktop/mobile layout, no JavaScript errors')
