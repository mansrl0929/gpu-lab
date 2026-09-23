#!/usr/bin/env bash
# GPU 호스트에 읽기 전용 수집기를 설치합니다 (Ubuntu).
#   sudo bash scripts/install_gpu_host.sh [포털이_도는_서버_IP]
# 하는 일: node_exporter(9100) + DCGM Exporter(9400) + GPU 프로세스 수집기.
# 하지 않는 일: 프로세스 종료, GPU 권한 변경, 사용자 계정 변경. 모두 읽기 전용입니다.
set -euo pipefail

PORTAL_IP="${1:-}"
DCGM_IMAGE="${DCGM_IMAGE:-nvcr.io/nvidia/k8s/dcgm-exporter:4.6.0-4.8.3-distroless}"
TEXTFILE_DIR=/var/lib/prometheus/node-exporter
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

[ "$(id -u)" -eq 0 ] || { echo "sudo로 실행하세요."; exit 1; }
command -v nvidia-smi >/dev/null || { echo "nvidia-smi를 찾을 수 없습니다. NVIDIA 드라이버를 먼저 설치하세요."; exit 1; }
nvidia-smi -L

echo "== 1/4 node_exporter =="
apt-get update -qq
apt-get install -y -qq prometheus-node-exporter
install -d -m 755 "$TEXTFILE_DIR" /opt/gpu-lab
ARGS_FILE=/etc/default/prometheus-node-exporter
touch "$ARGS_FILE"
if ! grep -q 'collector.textfile.directory' "$ARGS_FILE"; then
  # 기존 ARGS를 덮어쓰지 않고 textfile 수집 경로만 덧붙입니다.
  if grep -q '^ARGS=' "$ARGS_FILE"; then
    sed -i "s|^ARGS=\"\\(.*\\)\"|ARGS=\"\\1 --collector.textfile.directory=$TEXTFILE_DIR\"|" "$ARGS_FILE"
  else
    echo "ARGS=\"--collector.textfile.directory=$TEXTFILE_DIR\"" >> "$ARGS_FILE"
  fi
fi
systemctl enable --now prometheus-node-exporter
systemctl restart prometheus-node-exporter

echo "== 2/4 GPU 프로세스 수집기 =="
install -m 755 "$HERE/gpu-server/process_collector.py" /opt/gpu-lab/process_collector.py
install -m 644 "$HERE/gpu-server/lab-gpu-process.service" "$HERE/gpu-server/lab-gpu-process.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now lab-gpu-process.timer
systemctl start lab-gpu-process.service || true

echo "== 3/4 DCGM Exporter =="
if command -v docker >/dev/null; then
  docker rm -f gpu-lab-dcgm >/dev/null 2>&1 || true
  docker run -d --name gpu-lab-dcgm --restart unless-stopped \
    --gpus all --cap-add SYS_ADMIN --network host "$DCGM_IMAGE"
else
  echo "docker가 없어 DCGM Exporter를 건너뜁니다. 설치 후 다시 실행하세요:"
  echo "  sudo apt install -y docker.io && sudo bash $0 ${PORTAL_IP}"
fi

echo "== 4/4 방화벽 =="
if [ -n "$PORTAL_IP" ] && command -v ufw >/dev/null; then
  ufw allow from "$PORTAL_IP" to any port 9100 proto tcp
  ufw allow from "$PORTAL_IP" to any port 9400 proto tcp
  ufw deny 9100/tcp
  ufw deny 9400/tcp
  echo "ufw 규칙을 넣었습니다. 'sudo ufw status numbered'로 순서를 확인하세요."
  echo "ufw가 꺼져 있다면 SSH 허용 규칙을 먼저 확인한 뒤 'sudo ufw enable'을 실행하세요."
else
  echo "포털 IP를 주지 않았거나 ufw가 없어 방화벽 설정을 건너뜁니다."
  echo "9100/9400은 포털이 도는 서버에서만 접근 가능해야 합니다."
fi

echo
echo "== 확인 =="
sleep 3
curl -fsS http://127.0.0.1:9100/metrics | grep -c lab_gpu_process && echo "프로세스 메트릭 OK" || echo "프로세스 메트릭 없음: journalctl -u lab-gpu-process.service -n 30"
curl -fsS http://127.0.0.1:9400/metrics | grep -m1 DCGM_FI_DEV_GPU_UTIL && echo "DCGM OK" || echo "DCGM 없음: docker logs gpu-lab-dcgm"
echo "완료. 포털 서버의 nas/prometheus/prometheus.yml에 이 호스트 IP가 들어 있는지 확인하세요."
