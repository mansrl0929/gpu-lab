param([switch]$Demo)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 가상환경 생성 실패' }
}
& ./.venv/Scripts/python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw '의존성 설치 실패' }
if ($Demo) {
    $env:PORTAL_MODE = 'demo'
    Write-Host '데모 워크스페이스: 로그인 없이 예시 데이터를 둘러봅니다.' -ForegroundColor Cyan
} else {
    $env:PORTAL_MODE = 'standalone'
    $code = if ($env:PORTAL_SIGNUP_CODE) { $env:PORTAL_SIGNUP_CODE } elseif (Test-Path 'data/signup_code.txt') { (Get-Content 'data/signup_code.txt' -Raw).Trim() } else { '(서버 시작 로그에 표시됩니다)' }
    Write-Host "가입 코드: $code" -ForegroundColor Cyan
    Write-Host '첫 번째로 가입한 계정이 관리자입니다.' -ForegroundColor Cyan
}
Write-Host 'http://127.0.0.1:8000' -ForegroundColor Green
& ./.venv/Scripts/python.exe -m uvicorn portal.app:app --host 127.0.0.1 --port 8000
