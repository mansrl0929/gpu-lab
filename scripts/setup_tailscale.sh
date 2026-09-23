#!/usr/bin/env bash
# 고정 외부 주소 만들기 (Tailscale Funnel).
#   sudo bash scripts/setup_tailscale.sh [포트] [장비이름]
#   기본값: 포트 8000, 장비이름 igdsl-gpu  →  https://igdsl-gpu.<내tailnet>.ts.net
#   장비이름은 DNS 규칙에 맞춰 소문자로 바뀝니다 (iGDSL-GPU → igdsl-gpu).
#   뒤쪽 <내tailnet>은 Tailscale이 계정에 자동으로 붙이는 이름입니다.
#
# 접속하는 사람은 아무것도 설치하지 않아도 됩니다. 이 서버에만 설치합니다.
# 필요한 것: 무료 Tailscale 계정 (구글/깃허브 로그인 가능).
set -euo pipefail

PORT="${1:-8000}"
HOSTNAME_TAG="$(printf '%s' "${2:-iGDSL-GPU}" | tr '[:upper:]' '[:lower:]')"

[ "$(id -u)" -eq 0 ] || { echo "sudo를 붙여 실행하세요: sudo bash $0 $PORT $HOSTNAME_TAG"; exit 1; }

echo "== 1/5 Tailscale 설치 =="
if command -v tailscale >/dev/null; then
  echo "이미 설치되어 있습니다: $(tailscale version | head -1)"
else
  curl -fsSL https://tailscale.com/install.sh | sh
fi

echo
echo "== 2/5 로그인 =="
if tailscale status >/dev/null 2>&1; then
  echo "이미 로그인되어 있습니다."
else
  echo "잠시 뒤 주소가 하나 표시됩니다. 그 주소를 브라우저에서 열어 로그인하세요."
  echo "(계정이 없으면 구글/깃허브로 바로 만들 수 있습니다. 무료입니다.)"
  echo
  tailscale up --hostname="$HOSTNAME_TAG"
fi

echo
echo "== 3/5 외부 공개 (Funnel) =="
if tailscale funnel --bg "$PORT"; then
  echo "Funnel을 켰습니다."
else
  cat <<'GUIDE'

Funnel을 켜지 못했습니다. 보통 계정에서 기능을 한 번 허용해 주면 됩니다.

  1) https://login.tailscale.com/admin/dns 에서 HTTPS Certificates를 Enable
  2) https://login.tailscale.com/admin/acls 에서 Funnel 허용
     (위 명령이 안내한 링크를 그대로 열면 한 번에 켜집니다)
  3) 다시 실행: sudo tailscale funnel --bg 8000

GUIDE
  exit 1
fi

url="$(tailscale status --json 2>/dev/null | grep -o '"DNSName":"[^"]*"' | head -1 | cut -d'"' -f4 | sed 's/\.$//')"

echo
echo "== 4/5 HTTPS 인증서 =="
# 인증서가 발급되어야 공개 DNS에 주소가 등록됩니다. 이게 빠지면 "사이트에 연결할 수 없음"이 납니다.
if [ -n "$url" ] && tailscale cert "$url" >/dev/null 2>&1; then
  echo "인증서 준비 완료."
else
  cat <<GUIDE

인증서를 발급하지 못했습니다. 관리 콘솔에서 한 번만 켜 주세요.

  1) https://login.tailscale.com/admin/dns 에서 HTTPS Certificates를 Enable
  2) 서버에서 다시:
       sudo tailscale cert ${url:-<주소>}
       sudo tailscale funnel --bg $PORT

GUIDE
fi

echo
echo "== 5/5 주소 확인 =="
tailscale funnel status || true
if [ -n "$url" ]; then
  echo
  echo "외부 접속 주소: https://$url"
  echo "이 주소는 서버를 재시작해도 그대로입니다. 연구실에 이 주소와 가입 코드를 알려주세요."
  echo "밖에서 안 열리면 공개 DNS 등록까지 1~2분 걸릴 수 있습니다."
else
  echo "주소를 읽지 못했습니다. 'tailscale funnel status' 출력의 https:// 주소를 사용하세요."
fi
