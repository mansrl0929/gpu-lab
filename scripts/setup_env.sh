#!/usr/bin/env bash
# 편집기 없이 nas/.env를 채웁니다.
#   bash scripts/setup_env.sh <포털이_도는_서버_IP> [가입코드]
# 예:  bash scripts/setup_env.sh 10.174.52.127 iGDSL1234
set -euo pipefail

IP="${1:-}"
CODE="${2:-iGDSL1234}"
if [ -z "$IP" ]; then
  echo "사용법: bash scripts/setup_env.sh <서버 IP> [가입코드]"
  echo "예:     bash scripts/setup_env.sh 10.174.52.127 iGDSL1234"
  exit 1
fi

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/nas"
[ -f .env ] || cp .env.example .env

set_value() {
  if grep -q "^$1=" .env; then
    sed -i "s|^$1=.*|$1=$2|" .env
  else
    printf '%s=%s\n' "$1" "$2" >> .env
  fi
}

set_value NAS_BIND_IP 0.0.0.0
set_value NAS_PUBLIC_HOST "$IP"
set_value PORTAL_SIGNUP_CODE "$CODE"

# Grafana 비밀번호는 아직 예시값일 때만 무작위로 채웁니다.
if grep -q '^GRAFANA_ADMIN_PASSWORD=CHANGE_ME' .env; then
  password="$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 16 || true)"
  set_value GRAFANA_ADMIN_PASSWORD "$password"
fi
set_value HOMEPAGE_ALLOWED_HOSTS "localhost:3000,127.0.0.1:3000,$IP:3000"
chmod 600 .env

echo "nas/.env 를 아래와 같이 설정했습니다."
grep -E '^(NAS_BIND_IP|NAS_PUBLIC_HOST|PORTAL_SIGNUP_CODE|PORTAL_SECURE_COOKIES|GRAFANA_ADMIN_PASSWORD)=' .env
echo
echo "다음: docker compose up -d --build"
