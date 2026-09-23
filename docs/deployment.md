# 실제 장비 배포

## 1. 인벤토리

실제 NAS IP, DSM 버전, GPU 서버별 Linux 배포판/IP, GPU 개수/모델/index, 학생 Linux 계정, 내부 subnet, 백업 대상을 기록합니다. 워크스테이션이 한 대뿐이면 `config/resources.yml`의 servers/resources에서 두 번째 서버 항목과 해당 GPU를 지우면 됩니다. `config/resources.yml`에는 iGDSL-Aurora(RTX PRO 6000 Blackwell ×4)와 iGDSL-Polaris(RTX A6000 ×2)가 들어 있습니다. 다른 장비면 이 파일을 고칩니다. Windows GPU 호스트는 이 Linux용 DCGM·systemd·`/proc` 수집기를 그대로 실행할 수 없습니다. 실제 OS에 맞는 수집 방식을 별도로 구성해야 합니다.

GPU 호스트에서 `nvidia-smi`, `nvidia-smi -L`, `free -h`, `df -hT`, `docker info`를 점검합니다. NVIDIA Container Toolkit이 정상 동작해야 합니다. NAS에는 해당 DS1522+ 모델에 Package Center가 제공하는 호환 Container Manager를 설치합니다.

## 2. NAS 중앙 서비스

저장소 전체를 `/volume1/docker/gpu-lab`에 복사합니다. `nas/.env.example`을 `nas/.env`로 복사하고 모든 `CHANGE_ME`를 바꿉니다. `PORTAL_SIGNUP_CODE`는 연구실 인원에게만 공유할 가입 코드입니다. `NAS_PUBLIC_HOST`는 학생 브라우저에서 접근할 IP/호스트명, `NAS_BIND_IP`는 NAS 내부망 인터페이스 IP입니다. `HOMEPAGE_ALLOWED_HOSTS`에 실제 `hostname:3000`을 등록합니다.

포트: Homepage 3000 / 포털 8000 / Grafana 3001 (LibreBooking을 쓰면 8080). Prometheus 9090은 NAS localhost 전용입니다. 관리자는 SSH tunnel 또는 NAS 내부 CLI로 조회할 수 있습니다.

```sh
cd /volume1/docker/gpu-lab/nas
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 portal prometheus
```

Compose 파일은 named volume으로 DB/설정/시계열/Grafana를 보관합니다. volume 소유권을 컨테이너 사용자에 맞춰 유지합니다. 수동 삭제나 `docker compose down -v`는 데이터 손실을 유발하므로 정상 운영 명령에 사용하지 않습니다.

## 3. 포털 최초 설정

1. `http://NAS_IP:8000`을 엽니다. 계정이 하나도 없으면 회원가입 화면이 먼저 열립니다.
2. 아이디·사용자 이름·비밀번호와 `nas/.env`의 `PORTAL_SIGNUP_CODE`를 입력해 가입합니다. 이 첫 계정이 관리자입니다.
3. 나머지 연구실 인원에게 포털 주소와 가입 코드를 알려줍니다. 각자 같은 화면에서 가입하면 됩니다.
4. `config/resources.yml`의 워크스테이션·GPU 목록을 실제 장비에 맞게 수정하고 `docker compose up -d portal`로 반영합니다. 자원 매핑은 시작할 때 한 번 읽습니다.
5. 포털 아이디와 GPU 서버의 Linux 계정이 다른 사람이 있으면 `config/resources.yml`의 `users:`에 `portal_username`과 `linux_username`을 함께 적습니다. 없으면 포털 아이디를 Linux 계정명으로 간주합니다.

```yaml
users:
  - {portal_username: kimminsu, linux_username: student1, display_name: 김민수}
```

6. 가입 코드가 외부에 알려지면 `nas/.env`의 값을 바꾸고 `docker compose up -d portal`로 재시작합니다. 기존 계정과 예약은 그대로 유지됩니다.
7. 비밀번호 재설정 화면은 아직 없습니다. 잊어버린 계정은 관리자가 `portal` 볼륨의 `portal.sqlite3`에서 해당 사용자를 삭제한 뒤 다시 가입하게 합니다.

외부(교외)에서 접속하게 하려면 [공유·외부 접속](hosting.md)을 따르고, HTTPS 뒤에 둔 뒤 `PORTAL_SECURE_COOKIES=1`을 설정합니다.

### (선택) LibreBooking을 예약 시스템으로 쓰는 경우

이미 LibreBooking을 운영 중이라면 `PORTAL_MODE=live`로 두고 `docker compose --profile librebooking up -d`로 함께 기동합니다. 이때 포털은 읽기 전용이 되고, 계정·자원·예약 관리는 LibreBooking이 담당합니다. `/Web/install`에서 설치를 마치고, 시간대를 `Asia/Seoul`로, GPU마다 Resource를 등록한 뒤, 조회 전용 계정을 만들어 `nas/.env`의 `LIBREBOOKING_USERNAME`/`LIBREBOOKING_PASSWORD`에 넣고 `config/resources.yml`의 `librebooking_resource_id`·`librebooking_user_id`를 맞춥니다. LibreBooking persistent config에서 API를 활성화해야 합니다(`$conf['settings']['api']['enabled'] = 'true';`).

## 4. Linux GPU 호스트

node_exporter:

```sh
sudo apt update
sudo apt install -y prometheus-node-exporter
sudo install -d -m 755 /var/lib/prometheus/node-exporter /opt/gpu-lab
```

Ubuntu 패키지의 `/etc/default/prometheus-node-exporter`에서 기존 ARGS에 `--collector.textfile.directory=/var/lib/prometheus/node-exporter`를 추가합니다. 기존 설정을 덮어쓰지 말고 `systemctl cat prometheus-node-exporter`로 실제 패키지의 환경 파일과 실행 옵션을 확인합니다.

```sh
sudo systemctl enable --now prometheus-node-exporter
sudo systemctl restart prometheus-node-exporter
curl http://127.0.0.1:9100/metrics
```

`sudo bash scripts/install_gpu_host.sh <포털 서버 IP>`를 쓰면 아래 과정을 한 번에 처리합니다. 수동으로 하려면 `gpu-server` 디렉터리를 GPU 호스트로 복사한 뒤:

```sh
cd gpu-server
sudo docker compose up -d
sudo install -m 755 process_collector.py /opt/gpu-lab/process_collector.py
sudo install -m 644 lab-gpu-process.service lab-gpu-process.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now lab-gpu-process.timer
sudo systemctl start lab-gpu-process.service
curl http://127.0.0.1:9400/metrics
curl http://127.0.0.1:9100/metrics | grep lab_gpu_process
```

프로세스 수집기는 루트 권한으로 UID만 읽습니다. 명령행, 환경변수, 데이터셋 경로는 수집하지 않습니다. 프로세스 PID 라벨은 재실행 시 바뀌어 시계열 수를 늘릴 수 있으므로 연구실 규모를 넘어가면 사용자별 집계로 바꾸세요. MIG는 UUID 매핑 확장이 필요하며 현재는 미지원 장치 매핑을 확인 실패로 표시합니다.

## 5. 방화벽

GPU 호스트의 9100/9400은 NAS IP만 허용해야 합니다. DCGM Compose는 host networking을 사용하므로 Linux INPUT 방화벽 정책을 적용할 수 있습니다. Docker bridge published port의 UFW 우회 문제를 피하기 위한 구성입니다.

예시(NAS `192.168.0.20`, 실제 값으로 변경): 기존 SSH 허용 규칙·IPv6 정책·상위 네트워크 ACL을 먼저 확인하고 다음과 같은 allow-first/deny-next 규칙을 적용합니다.

```sh
sudo ufw allow from 192.168.0.20 to any port 9100 proto tcp
sudo ufw allow from 192.168.0.20 to any port 9400 proto tcp
sudo ufw deny 9100/tcp
sudo ufw deny 9400/tcp
sudo ufw status numbered
```

기존 넓은 허용 규칙이 앞에 있으면 이 정책이 무효일 수 있습니다. 별도 SSH 세션에서 접근 유지 여부를 확인하고 방화벽을 활성화합니다. NAS 측 Container Manager/DSM 방화벽에서도 학생용 포트만 연구실 subnet에 허용합니다. DSM/Prometheus/exporter를 인터넷에 직접 공개하지 않습니다.

## 6. 최종 확인

- NAS에서 두 서버의 9100/9400에 접근 가능, 일반 학생 단말에서는 접근 차단.
- Prometheus `/targets`에서 node-exporter/DCGM/portal 모두 UP.
- Prometheus query `lab_gpu_process_collector_success`가 1이며 timestamp가 최신.
- 10분 수집 후 idle GPU가 UNKNOWN에서 FREE/RESERVED_IDLE로 전환.
- Grafana에 provisioned Prometheus와 Overview/Server detail 대시보드 표시.
- Prometheus `/rules`에 기록 규칙(`lab:gpu_utilization:avg7d`, `lab:gpu_utilization:avg30d`, `lab:gpu_user_active:max`)과 알림 규칙이 로드됨.
- `curl localhost:8000/metrics`에 `lab_gpu_busy`, `lab_gpu_borrowed`가 나오고 Prometheus `gpu-portal` target이 UP.
- 운영 리포트(`#reports`)는 연동 직후 값이 비어 있거나 수집률이 낮은 것이 정상이며, 수집이 쌓이면서 채워집니다. 7일·30일 수치는 그만큼 수집한 뒤 판단하세요.
- 실제 사용자 계정의 단일/멀티 GPU 예약, 중복 거절, 수정/취소, 권한 검증.
- 예약 후에도 Linux/CUDA 실행 가능. 어떠한 자동 kill도 없음.
- NAS 및 GPU 호스트 재부팅 후 서비스 자동 복구.

프로세스 수집기를 설치하지 않아도 Grafana utilization은 보이지만 포털의 Borrowable 판정은 보수적으로 UNKNOWN이 될 수 있습니다.

[iGDSL 설치 실행서](quickstart-igdsl.md) · [시스템 구조](architecture.md) · [공유·외부 접속](hosting.md) · [운영·백업](operations.md) · [장애 대응](troubleshooting.md)
