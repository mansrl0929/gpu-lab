#!/usr/bin/env bash
# GPU 서버에 읽기 전용 수집기를 설치합니다 (Ubuntu).
#   sudo bash scripts/install_gpu_host.sh [포털이_도는_서버_IP]
#
# 설치하는 것: node_exporter(9100) + GPU 수집기(10초마다 nvidia-smi 읽기).
# Docker도, NVIDIA 컨테이너 툴킷도 필요 없습니다.
# 하지 않는 것: 프로세스 종료, GPU 권한 변경, 계정 변경. 전부 읽기 전용입니다.
set -euo pipefail

PORTAL_IP="${1:-}"
TEXTFILE_DIR=/var/lib/prometheus/node-exporter
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

[ "$(id -u)" -eq 0 ] || { echo "sudo를 붙여서 실행하세요: sudo bash $0 ${PORTAL_IP:-<포털IP>}"; exit 1; }
command -v nvidia-smi >/dev/null || { echo "nvidia-smi를 찾을 수 없습니다. NVIDIA 드라이버를 먼저 설치하세요."; exit 1; }
echo "== GPU 확인 =="
nvidia-smi --query-gpu=index,name --format=csv,noheader

echo
echo "== 1/4 node_exporter 설치 =="
apt-get update -qq
apt-get install -y -qq prometheus-node-exporter python3
install -d -m 755 "$TEXTFILE_DIR" /opt/gpu-lab

ARGS_FILE=/etc/default/prometheus-node-exporter
touch "$ARGS_FILE"
if ! grep -q 'collector.textfile.directory' "$ARGS_FILE"; then
  # 기존 ARGS를 지우지 않고 textfile 수집 경로만 덧붙입니다.
  if grep -q '^ARGS=' "$ARGS_FILE"; then
    sed -i "s|^ARGS=\"\\(.*\\)\"|ARGS=\"\\1 --collector.textfile.directory=$TEXTFILE_DIR\"|" "$ARGS_FILE"
  else
    echo "ARGS=\"--collector.textfile.directory=$TEXTFILE_DIR\"" >> "$ARGS_FILE"
  fi
fi
systemctl enable --now prometheus-node-exporter
systemctl restart prometheus-node-exporter

echo "== 2/4 GPU 수집기 등록 =="
install -m 755 "$HERE/gpu-server/process_collector.py" /opt/gpu-lab/process_collector.py
install -m 644 "$HERE/gpu-server/lab-gpu-process.service" "$HERE/gpu-server/lab-gpu-process.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now lab-gpu-process.timer
systemctl start lab-gpu-process.service || true

echo "== 3/4 방화벽 =="
if [ -n "$PORTAL_IP" ] && command -v ufw >/dev/null; then
  ufw allow from "$PORTAL_IP" to any port 9100 proto tcp
  ufw deny 9100/tcp
  echo "9100 포트를 $PORTAL_IP 에서만 열었습니다. 'sudo ufw status numbered'로 확인하세요."
  echo "ufw가 꺼져 있다면 SSH 허용을 먼저 확인한 뒤 'sudo ufw enable'을 실행하세요."
else
  echo "포털 IP를 안 주셨거나 ufw가 없어 방화벽 설정을 건너뜁니다."
  echo "9100 포트는 포털이 도는 서버에서만 접근 가능해야 합니다."
fi

echo "== 4/4 확인 =="
sleep 12   # 수집기 타이머가 한 번 돌 때까지 기다립니다.
# 파이프로 바로 grep하면 curl이 SIGPIPE로 죽어 오탐이 납니다. 먼저 받아두고 검사합니다.
metrics="$(curl -fsS http://127.0.0.1:9100/metrics || true)"
if printf '%s' "$metrics" | grep -q '^DCGM_FI_DEV_GPU_UTIL'; then
  echo "OK: GPU 사용률이 수집되고 있습니다."
  printf '%s' "$metrics" | grep -E '^DCGM_FI_DEV_GPU_UTIL|^lab_gpu_process_info' | head -8
else
  echo "아직 GPU 값이 없습니다. 아래로 원인을 확인하세요:"
  echo "  systemctl status lab-gpu-process.service --no-pager"
  echo "  journalctl -u lab-gpu-process.service -n 30 --no-pager"
fi
echo
echo "끝났습니다. 포털 서버의 nas/prometheus/prometheus.yml에 이 서버 IP가 있는지 확인하세요."
