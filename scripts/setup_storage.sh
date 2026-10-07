#!/usr/bin/env bash
# 데이터 디스크에 사용자별 폴더를 만들고, 사용량 수집기를 등록합니다.
#   sudo bash scripts/setup_storage.sh /home /mnt/nas
#   sudo bash scripts/setup_storage.sh --measure-only /home   (폴더를 만들지 않음)
#
# 하는 일: 각 디스크에 로그인 계정 이름으로 폴더 생성(없을 때만) + 30분마다 용량 측정.
# 하지 않는 일: 기존 파일 이동·삭제, 권한 변경. 이미 있는 폴더는 건드리지 않습니다.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ "$(id -u)" -eq 0 ] || { echo "sudo를 붙여 실행하세요: sudo bash $0 /home /mnt/nas"; exit 1; }
MEASURE_ONLY=0
if [ "${1:-}" = "--measure-only" ]; then MEASURE_ONLY=1; shift; fi
[ "$#" -ge 1 ] || { echo "디스크 경로를 하나 이상 적어주세요. 예) sudo bash $0 /home /mnt/nas"; exit 1; }

echo "== 1/4 디스크 확인 =="
for mount in "$@"; do
  [ -d "$mount" ] || { echo "경로가 없습니다: $mount"; exit 1; }
  df -h --output=target,size,used,avail "$mount" | tail -1
done

echo
echo "== 2/4 사용자 폴더 =="
users="$(awk -F: '$3>=1000 && $3<65534 && $7 !~ /(nologin|false)$/ {print $1}' /etc/passwd | sort)"
[ -n "$users" ] || { echo "일반 사용자 계정을 찾지 못했습니다."; exit 1; }
if [ "$MEASURE_ONLY" = "1" ]; then
  echo "  --measure-only: 폴더를 만들지 않고 용량만 측정합니다."
fi
for mount in "$@"; do
  if [ "$MEASURE_ONLY" = "1" ]; then continue; fi
  case "$(stat -f -c %T "$mount" 2>/dev/null)" in
    cifs|smb2|nfs|nfs4) echo "  $mount 은 네트워크 공유라 폴더를 만들지 않습니다 (용량만 측정)."; continue;;
  esac
  for user in $users; do
    target="$mount/$user"
    if [ -d "$target" ]; then
      echo "  이미 있음: $target"
    else
      install -d -m 0755 -o "$user" -g "$(id -gn "$user")" "$target"
      echo "  만듦:      $target"
    fi
  done
done

echo
echo "== 3/4 수집기 등록 =="
install -d -m 755 /etc/gpu-lab /opt/gpu-lab
printf '%s\n' "$@" > /etc/gpu-lab/storage.conf
install -m 755 "$HERE/gpu-server/storage_collector.py" /opt/gpu-lab/storage_collector.py
install -m 644 "$HERE/gpu-server/lab-gpu-storage.service" "$HERE/gpu-server/lab-gpu-storage.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now lab-gpu-storage.timer

echo
echo "== 4/4 첫 측정 =="
echo "용량이 크면 몇 분 걸립니다. 기다리는 중..."
systemctl start lab-gpu-storage.service || true
metrics="$(curl -fsS http://127.0.0.1:9100/metrics || true)"
if printf '%s' "$metrics" | grep -q '^lab_storage_total_bytes'; then
  echo "OK: 용량이 수집되고 있습니다."
  printf '%s' "$metrics" | grep '^lab_storage_user_bytes' | head -6
else
  echo "아직 값이 없습니다. 측정이 끝나면 올라옵니다. 진행 상황:"
  echo "  journalctl -u lab-gpu-storage.service -n 20 --no-pager"
fi
echo
echo "끝났습니다. 포털의 '저장공간' 메뉴에서 확인하세요."
