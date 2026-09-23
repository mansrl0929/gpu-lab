# GPU LAB — 연구실 GPU 예약·모니터링

**Soft Reservation — no enforcement.** 예약은 우선 사용권입니다. 이 시스템은 GPU 권한 변경, SSH 제어, 프로세스 종료를 하지 않습니다.

제공된 `GPU_Lab_Reservation_Monitoring_Guide_DS1522+.docx`를 기반으로 구현했습니다. 포털이 **계정과 예약을 직접 관리**하고, GPU 실사용 상태는 **Prometheus · DCGM Exporter · node_exporter**에서 읽습니다. 프런트엔드 빌드나 Node.js 없이 실행합니다.

## 지금 실행하기

Windows PowerShell:

```powershell
cd D:\web
.\run.ps1
```

브라우저에서 **http://127.0.0.1:8000** → 회원가입 화면이 뜹니다. **아이디 · 사용자 이름 · 비밀번호 · 연구실 가입 코드**를 입력하면 바로 사용할 수 있습니다. 맨 처음 가입한 계정에는 다른 사람 예약을 정리할 수 있는 권한이 함께 붙습니다.

가입 코드는 `PORTAL_SIGNUP_CODE` 환경변수에서, 없으면 `data/signup_code.txt`에서 읽습니다. 둘 다 없으면 시작할 때 무작위로 만들어 로그에 한 번 출력합니다. 두 경로 모두 git에 올라가지 않습니다.

Linux/macOS:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
PORTAL_SIGNUP_CODE=연구실코드 uvicorn portal.app:app --host 127.0.0.1 --port 8000
```

예시 데이터를 로그인 없이 둘러보려면:

```powershell
.\run.ps1 -Demo
```

## 실행 모드

| 모드 | 예약 저장 | 계정 | 용도 |
|---|---|---|---|
| `standalone` (기본) | 포털 SQLite | 포털 회원가입 (가입 코드) | 실제 운영 |
| `demo` | 예시 데이터 | 없음 (익명 미리보기) | 화면 확인·스크린샷 |
| `live` | LibreBooking | LibreBooking | LibreBooking을 이미 운영 중일 때 |

`PORTAL_MODE`로 선택합니다. `live`에서는 포털의 예약 쓰기 API가 차단되고 예약 관리는 LibreBooking이 담당합니다.

## 구현 기능

| 기능 | 구현 방식 |
|---|---|
| 계정 | 가입 코드 기반 회원가입, PBKDF2-SHA256(240k) 비밀번호, 14일 HttpOnly 세션 쿠키, 로그인 시도 제한, 첫 계정 = 관리자 |
| 한국어 통합 대시보드 | 서버별 GPU 카드, 상태·사용자 검색, 10초 자동 갱신, 모바일 반응형 |
| 예약 캘린더 | KST 일별 타임라인, 날짜 이동, 멀티 GPU 예약, 상세 보기 |
| 예약 생성·수정·취소 | SQLite 트랜잭션·중복 검사, 본인 예약만 수정(관리자는 전체) |
| 예약 추가 항목 | 프로젝트, CPU 코어, RAM, 작업 유형, 48시간 초과 사유 |
| GPU·서버 상태 | CPU/RAM/Disk, GPU utilization/VRAM/온도/전력 |
| 예약·실사용 통합 | FREE, RESERVED_IDLE, RESERVED_IN_USE, UNRESERVED_IN_USE, BORROWED, CONFLICT, OFFLINE, UNKNOWN |
| GPU 수치·실사용자 | `nvidia-smi` + `/proc` 읽기 전용 수집기가 node_exporter textfile로 발행 (GPU 서버에 Docker 불필요) |
| 추이·통계 | 24시간/7일 GPU 사용률, 관측된 예약 시간·예약 중 실사용 비율·수집률 |
| 운영 리포트 | 7일/30일 평균 사용률, 실사용·유휴 GPU-h, 학생별 GPU-hour, 멀티 GPU 예약 비율, CPU/RAM 포화 시간대 |
| 알림 | 대여·조정 필요·오프라인·확인 필요·예약 종료 후 사용 지속 알림, Prometheus Offline/온도/XID/수집기 지연/대여 지속/포털 중단 규칙 |
| 운영 배포 | NAS Compose(포털·Prometheus·Grafana·Homepage), exporter 구성, Grafana 대시보드 자동 등록 |
| 운영 문서 | 시스템 구조, 설치, 공유·외부 접속, 백업/복구, 장애 대응·훈련, 인수 체크리스트 |

모니터링은 원문 원칙대로 읽기 전용입니다. 예약은 우선 사용권 표시이며 자동 kill이나 권한 변경은 없습니다.

## 실제 워크스테이션 연결

iGDSL 장비가 이미 설정되어 있습니다.

| 장비 | 주소 | GPU |
|---|---|---|
| iGDSL-Aurora | `10.174.52.127` | RTX PRO 6000 Blackwell × 4 (96 GB) |
| iGDSL-Polaris | `10.174.52.130` | RTX A6000 × 2 (48 GB) |

설치는 **[처음 설치하기 — 단계별 따라하기](docs/setup-step-by-step.md)** 를 그대로 따라가면 됩니다(요약본은 [iGDSL 실행서](docs/quickstart-igdsl.md)). GPU 서버에는 Docker 없이 스크립트 한 줄이면 됩니다.

```sh
sudo bash scripts/install_gpu_host.sh <포털이 도는 서버 IP>
```

장비를 바꾸면 `config/resources.yml`(GPU 목록)과 `nas/prometheus/prometheus.yml`(exporter 주소) 두 파일만 수정합니다.

모니터링을 아직 연결하지 않아도 계정·예약·캘린더는 그대로 동작하며, 화면에는 "모니터링 미연동"으로 표시됩니다. 사용률과 운영 리포트는 연동 후부터 쌓입니다. 화면은 10초, GPU 메트릭은 10초, 프로세스 사용자는 30초 주기로 갱신됩니다.

```sh
cd nas
cp .env.example .env
# .env, config/resources.yml, prometheus.yml을 실제 값으로 수정한 후
docker compose config --quiet
docker compose up -d --build
```

배포는 저장소 전체를 `/volume1/docker/gpu-lab` 등에 복사한 뒤 `nas/docker-compose.yml`로 실행합니다. 상위 디렉터리의 `portal`, `config`, `grafana`, `Dockerfile`도 필요합니다. `NAS_BIND_IP` 기본값은 localhost입니다. 실제 서비스에는 NAS 내부망 IP를 지정하고 DSM 방화벽에서 교내/연구실 네트워크만 허용하세요. Prometheus는 localhost에만 바인딩합니다. LibreBooking을 함께 쓰려면 `docker compose --profile librebooking up -d`로 별도 기동합니다.

이 작업 환경에서는 Docker 엔진이 실행되지 않아 컨테이너 실제 기동·NAS 재부팅·실제 GPU 수집은 검증하지 않았습니다. Compose 문법과 로컬 앱은 검증했습니다.

## 외부에서 접속하게 하려면

연구실 밖에서도 쓰려면 Cloudflare Tunnel로 HTTPS 주소를 만듭니다. 포트를 열지 않습니다.

```sh
# 임시 주소 (계정 불필요, 명령을 끄면 사라짐)
docker run --rm --network host cloudflare/cloudflared tunnel --url http://localhost:8000

# 고정 주소: nas/.env에 TUNNEL_TOKEN과 PORTAL_SECURE_COOKIES=1을 넣고
docker compose --profile tunnel up -d
```

자세한 절차와 점검표는 [공유·외부 접속](docs/hosting.md), 이 연구실 기준 실행서는 [iGDSL 설치 실행서](docs/quickstart-igdsl.md)에 있습니다. Git 공유 시 `.env`와 `data/`(계정 DB·가입 코드)는 `.gitignore`로 제외됩니다.

## 데이터와 상태 판정

- 프로세스가 있거나 VRAM이 256 MiB 초과이거나 최근 10분 utilization 평균이 5% 이상이면 Busy로 판단합니다.
- Borrowable은 프로세스 수집이 정상이고, 프로세스가 없으며, 낮은 VRAM·평균 사용률과 충분한 최근 수집 표본이 확인될 때만 표시합니다.
- 10분 내 최소 18개 GPU 표본(현재 10초 수집이므로 약 60개), 90초 이내 프로세스 수집, 정상 exporter를 요구합니다. 미확인 데이터는 UNKNOWN/OFFLINE이며 임의로 FREE로 바꾸지 않습니다.
- 모니터링을 연결하지 않은 상태는 장애가 아니라 "미연동"으로 구분해 표시합니다. 외부 API 오류 시 데모 데이터로 대체하지 않습니다.
- 예약자만 실행 중이면 RESERVED_IN_USE, 다른 사용자만 실행 중이면 BORROWED, 예약자와 다른 사용자 동시 실행/예약 중첩은 CONFLICT입니다. 실제 대여 합의 여부를 판정하지는 않습니다.
- 예약자의 포털 아이디와 Linux 계정이 다르면 `config/resources.yml`의 `users:`에 `portal_username ↔ linux_username`을 추가합니다. 없으면 포털 아이디를 Linux 계정명으로 간주합니다.
- 예약 대비 실사용 통계는 `/metrics`를 Prometheus가 수집한 시점부터 쌓입니다. 모르는 상태는 표본에서 제외하며 수집률을 함께 표시합니다. 과거 예약을 수정·삭제해도 관측 기록은 다시 쓰이지 않습니다.
- 운영 리포트(7일/30일)는 Prometheus 보관 기간과 연동 시작 이후 구간만 집계합니다. 증설·재배치 판단용이며 학생 평가용이 아닙니다.

## 테스트

```powershell
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m playwright install chromium
# 데모 서버를 먼저 실행한 뒤 (.\run.ps1 -Demo)
.\.venv\Scripts\python scripts/browser_check.py
docker compose --env-file nas/.env.example -f nas/docker-compose.yml config --quiet
```

`pytest` 51개가 상태 판정·예약·계정·리포트·연동 계약을 검증합니다. `browser_check.py`는 데모 워크스페이스(8000)와 자체 실행하는 standalone 워크스페이스(8101)에서 가입·로그인·예약 흐름을 확인하고 스크린샷을 `artifacts/`에 남깁니다. 프런트엔드는 외부 CDN/폰트 없이 내부망에서 동작합니다. Grafana JSON은 `python scripts/build_dashboards.py`로 재생성합니다.

## 디렉터리

```text
portal/                 FastAPI, 계정·세션·예약, 외부 API 통합, 한국어 웹 UI
config/resources.yml    GPU/사용자 매핑
nas/                    Compose, Prometheus, Homepage, (선택) LibreBooking
gpu-server/             DCGM Compose, 프로세스 수집기, systemd unit
grafana/                Overview/Detail JSON, provisioning
docs/                   구조·배포·공유·운영·장애 대응·인수 기준
tests/                  상태·예약·계정·연동 계약 테스트
scripts/                브라우저 검증, Grafana 생성
```

[처음 설치하기](docs/setup-step-by-step.md) · [iGDSL 실행서](docs/quickstart-igdsl.md) · [시스템 구조](docs/architecture.md) · [배포](docs/deployment.md) · [공유·외부 접속](docs/hosting.md) · [운영 및 백업](docs/operations.md) · [장애 대응](docs/troubleshooting.md) · [기능별 인수 기준](docs/acceptance.md)

공식 연동 근거: [Prometheus HTTP API](https://prometheus.io/docs/prometheus/latest/querying/api/), [DCGM Exporter](https://github.com/NVIDIA/dcgm-exporter), [node_exporter](https://github.com/prometheus/node_exporter), [LibreBooking API](https://librebooking.readthedocs.io/en/latest/API.html), [LibreBooking Docker](https://github.com/LibreBooking/docker).
