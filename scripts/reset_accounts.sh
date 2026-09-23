#!/usr/bin/env bash
# 포털의 계정과 예약을 전부 지우고 처음 상태로 되돌립니다.
#   bash scripts/reset_accounts.sh --yes
# 지워지는 것: 모든 계정, 세션, 예약.  남는 것: 가입 코드, 설정, 수집된 사용량 기록.
set -euo pipefail

if [ "${1:-}" != "--yes" ]; then
  echo "계정과 예약을 모두 삭제합니다. 되돌릴 수 없습니다."
  echo "정말 지우려면: bash scripts/reset_accounts.sh --yes"
  exit 1
fi

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/nas"

if docker compose ps --format '{{.Service}}' 2>/dev/null | grep -q '^portal$'; then
  stamp="$(date +%Y%m%d-%H%M%S)"
  mkdir -p backups
  # 혹시 모르니 지우기 전에 사본을 하나 남깁니다.
  docker compose cp "portal:/app/data/portal.sqlite3" "backups/portal-before-reset-$stamp.sqlite3" 2>/dev/null \
    && echo "삭제 전 사본: nas/backups/portal-before-reset-$stamp.sqlite3" \
    || echo "기존 계정 파일이 없어 사본은 건너뜁니다."
  docker compose exec -T portal sh -c 'rm -f /app/data/portal.sqlite3 /app/data/portal.sqlite3-wal /app/data/portal.sqlite3-shm'
  docker compose restart portal >/dev/null
  echo "계정과 예약을 삭제했습니다. 포털을 다시 시작했습니다."
else
  echo "portal 컨테이너를 찾을 수 없습니다. nas 디렉터리에서 docker compose ps 를 확인하세요."
  exit 1
fi

echo
echo "이제 브라우저에서 새로 가입하면 됩니다. 가입 코드:"
grep '^PORTAL_SIGNUP_CODE=' .env || echo "  (.env에 PORTAL_SIGNUP_CODE 없음)"
