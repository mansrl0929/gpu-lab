#!/usr/bin/env bash
# 지금 살아 있는 외부 접속 주소를 알려줍니다.
#   bash scripts/tunnel_url.sh
# 임시 터널(trycloudflare) 주소는 컨테이너가 다시 뜰 때마다 바뀌므로 항상 마지막 것을 씁니다.
set -uo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/nas"

state="$(docker compose ps --format '{{.Service}} {{.State}}' 2>/dev/null | grep '^quicktunnel ' || true)"
if [ -z "$state" ]; then
  echo "quicktunnel이 실행 중이 아닙니다. 먼저 실행하세요:"
  echo "  cd ~/gpu-lab/nas && docker compose --profile quicktunnel up -d"
  exit 1
fi
echo "컨테이너 상태: $state"

url="$(docker compose logs --tail=500 quicktunnel 2>/dev/null | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -1 || true)"
if [ -z "$url" ]; then
  echo "아직 주소가 발급되지 않았습니다. 20초쯤 뒤 다시 실행하거나 로그를 보세요:"
  echo "  docker compose logs --tail=50 quicktunnel"
  exit 1
fi

echo
echo "외부 접속 주소: $url"
echo
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$url/api/health" || true)"
case "$code" in
  200) echo "확인: 정상 동작합니다. 이 주소와 가입 코드를 연구실에 알려주세요." ;;
  53*) echo "확인: 터널이 아직 연결되지 않았습니다($code). 20초 뒤 다시 실행해 보세요." ;;
  *)   echo "확인: 예상치 못한 응답($code). docker compose logs --tail=50 quicktunnel 로 원인을 보세요." ;;
esac
