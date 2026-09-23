# 시스템 구조

원문 가이드 2장·13장의 구조를 이 저장소의 실제 파일과 함께 정리합니다. 전체 원칙은 하나입니다. **예약은 우선 사용권이고, 모니터링은 읽기 전용입니다.** 어떤 구성요소도 GPU 권한을 바꾸거나 프로세스를 종료하지 않습니다.

## 구성요소

| 계층 | 구성요소 | 위치 | 역할 |
|---|---|---|---|
| 중앙(NAS) | 통합 포털 (FastAPI) | `portal/`, `Dockerfile` | **계정·예약의 원본**, 예약+실사용 결합 화면, 상태 판정, 운영 리포트 |
| 선택 | LibreBooking + MariaDB | `nas/docker-compose.yml` (`--profile librebooking`) | `PORTAL_MODE=live`일 때만 예약의 원본 |
| 중앙(NAS) | Prometheus | `nas/prometheus/` | GPU 10초·서버 15초·포털 30초 수집, 기록 규칙, 알림 규칙 |
| 중앙(NAS) | Grafana | `grafana/` | 서버·GPU 상세 시각화, 장기 추이 |
| 중앙(NAS) | Homepage | `nas/homepage/` | 학생용 단일 진입점 |
| GPU 호스트 | DCGM Exporter | `gpu-server/docker-compose.yml` | GPU utilization/VRAM/온도/전력/XID |
| GPU 호스트 | node_exporter | OS 패키지 | CPU/RAM/Disk, textfile collector |
| GPU 호스트 | 프로세스 수집기 | `gpu-server/process_collector.py` | `nvidia-smi` + `/proc`로 GPU별 Linux 사용자 |

기본 모드(`PORTAL_MODE=standalone`)에서 계정과 예약의 원본은 포털의 SQLite(`data/portal.sqlite3`, 컨테이너에서는 `portal` 볼륨)이고, 메트릭의 원본은 Prometheus입니다. `live` 모드에서는 예약의 원본이 LibreBooking으로 바뀌고 포털의 예약 쓰기 API가 차단됩니다. `demo` 모드의 SQLite(`data/demo.sqlite3`)는 로컬 미리보기 전용입니다.

계정은 가입 코드로만 만들 수 있고, 비밀번호는 PBKDF2-SHA256(240,000회)로 해시해 저장합니다. 세션은 서버에 토큰의 SHA-256만 남기고 브라우저에는 HttpOnly 쿠키로 14일간 유지합니다. 첫 계정이 관리자이며, 관리자만 다른 사람의 예약을 수정·취소할 수 있습니다.

## 데이터 흐름

```text
학생 브라우저
  ├─ Homepage(3000) ─▶ 바로가기
  ├─ 통합 포털(8000) ─┬─ 회원가입 / 로그인 (세션 쿠키)
  │                   ├─ 예약 생성·수정·취소 ─▶ portal.sqlite3
  │                   └─▶ Prometheus HTTP API (GPU·서버 메트릭, 프로세스 사용자)
  └─ Grafana(3001) ─▶ Prometheus 시각화

GPU 호스트 ──(30초 scrape)──▶ Prometheus ──▶ Grafana / 포털
통합 포털 /metrics ──(30초 scrape)──▶ Prometheus  (예약·Busy 관측 기록)

선택: PORTAL_MODE=live ─▶ 예약의 원본이 LibreBooking(8080)으로 바뀜
```

포털의 `/metrics`는 포털이 판정한 상태를 Prometheus가 기록하게 하는 경로입니다. 이 기록으로 "예약된 시간 대비 실제 Busy 시간" 같은 장기 통계를 예약 DB를 다시 쓰지 않고 계산합니다. 과거 예약을 수정·삭제해도 이미 관측된 기록은 바뀌지 않습니다.

## 포털 내부 구조

| 파일 | 책임 |
|---|---|
| `portal/app.py` | 라우팅, 인증 게이트, 스냅샷 조립, 예약 API, `/metrics` 노출 |
| `portal/accounts.py` | 비밀번호 해시·검증, 세션 토큰, 가입 값 검증, 로그인 시도 제한 |
| `portal/store.py` | 계정·세션·예약 SQLite 저장소, 소유자/관리자 권한 판정 |
| `portal/state.py` | GPU 상태 8종 판정 (FREE … UNKNOWN) |
| `portal/alerts.py` | 화면 알림 생성 (대여/조정 필요/오프라인/예약 종료 후 사용 지속) |
| `portal/reports.py` | 예약 집계, 사용자 GPU-hour 병합, 시간대별 포화 프로파일 |
| `portal/integrations.py` | LibreBooking·Prometheus 호출과 응답 계약 |
| `portal/demo.py` | 데모 데이터(예시 장비·예약·리포트) |
| `config/resources.yml` | 워크스테이션·GPU 목록, 포털 아이디 ↔ Linux 계정 매핑(선택), LibreBooking resource ID(선택) |

### API

| 엔드포인트 | 내용 |
|---|---|
| `GET /api/session` | 로그인 여부, 현재 사용자, 첫 계정 여부, 모니터링 연동 여부 |
| `POST /api/auth/signup` | 가입 코드 확인 후 계정 생성, 세션 시작 |
| `POST /api/auth/login` / `logout` | 로그인(시도 제한 포함) / 세션 종료 |
| `GET /api/members` | 연구실 구성원 목록(역할은 관리자에게만) |
| `GET /api/snapshot` | GPU별 상태·예약·메트릭, 서버 요약, 알림, 연동 오류, 현재 사용자 |
| `GET /api/history?hours=24\|168` | GPU별 utilization 시계열 |
| `GET /api/statistics?hours=24\|168` | 예약 시간·예약 중 사용 비율·수집률 |
| `GET /api/reports?days=7\|30` | 평균 사용률, Busy/Idle, 학생별 GPU-hour, 멀티 GPU 비율, CPU/RAM 포화 시간대 |
| `GET /metrics` | `lab_gpu_reserved`, `lab_gpu_reserved_busy`, `lab_gpu_busy`, `lab_gpu_borrowed` |
| `POST/PUT/DELETE /api/reservations` | 예약 생성·수정·취소. 본인 예약만(관리자는 전체), live 모드에서는 403 |

### 상태 판정

`portal/state.py`는 원문 12.3의 권고대로 프로세스 유무를 먼저 보고, VRAM과 최근 10분 평균 사용률을 함께 사용합니다. 판정 근거가 부족하면 FREE로 낙관하지 않고 UNKNOWN/OFFLINE으로 표시합니다.

| 상태 | 조건 |
|---|---|
| `OFFLINE` | GPU 메트릭 없음 또는 exporter DOWN |
| `UNKNOWN` | 예약 조회 실패, 프로세스 수집 불가, 표본 부족 |
| `FREE` | 예약 없음 + 유휴 (프로세스 0, 낮은 VRAM·사용률, 표본 충분) |
| `RESERVED_IDLE` | 예약 있음 + 유휴 → 화면에 Borrowable |
| `RESERVED_IN_USE` | 예약자 = 실사용자 |
| `UNRESERVED_IN_USE` | 예약 없이 사용 중 |
| `BORROWED` | 예약자와 다른 사용자가 사용 중 |
| `CONFLICT` | 예약 중첩 또는 예약자와 다른 사용자가 동시 실행 |

## 네트워크와 포트

| 서비스 | 포트 | 접근 |
|---|---|---|
| Homepage | 3000 | 내부망 |
| 통합 포털 | 8000 | 내부망 |
| LibreBooking | 8080 | 내부망 |
| Grafana | 3001 | 내부망 |
| Prometheus | 9090 | NAS localhost 전용 |
| node_exporter / DCGM | 9100 / 9400 | NAS IP만 허용 |

외부 공개가 필요하면 [공유·외부 접속](hosting.md)의 절차대로 VPN 또는 HTTPS reverse proxy/터널을 앞에 둡니다. DSM 관리 포트와 exporter 포트는 인터넷에 포트포워딩하지 않습니다.

## 의도적으로 구현하지 않은 것

GPU 장치 은닉, `CUDA_VISIBLE_DEVICES` 강제, 예약 종료 시 프로세스 kill, queue/fair-share/preemption, Slurm·Kubernetes 전환, NAS에서의 GPU 연산. 이 경계는 원문 1.3의 비목표와 같습니다.

[배포](deployment.md) · [공유·외부 접속](hosting.md) · [운영·백업](operations.md) · [장애 대응](troubleshooting.md) · [인수 기준](acceptance.md)
