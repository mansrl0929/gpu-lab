# 장애 대응

증상 → 확인 순서입니다. 모든 조치는 읽기·재시작 범위이며, 학생의 학습 프로세스를 종료하는 절차는 포함하지 않습니다.

## 1차 점검표

| 증상 | 우선 확인 |
|---|---|
| 포털 전체 연결 실패 | 컨테이너 로그, 8000 포트, reverse proxy, `/api/health` |
| 예약 정보 확인 불가 | LibreBooking API 활성화, 조회 계정 권한/암호, API URL, MariaDB |
| GPU OFFLINE | 서버 전원/네트워크 → DCGM → `nvidia-smi` → 9400 → NAS 방화벽 |
| CPU/RAM만 없음 | node_exporter service, 9100, `server` 라벨 |
| GPU utilization 0인데 UNKNOWN | 프로세스 수집기 success/timestamp, `/proc` 접근, 10분 초기 수집 대기 |
| Grafana No data | Prometheus `/targets`, datasource uid, 라벨·쿼리 |
| Homepage만 오류 | allowed hosts에 실제 `hostname:3000` 포함 여부 |
| 예약 대비 통계 없음 | portal target UP, `/metrics`, 수집 시작 시각, UNKNOWN 제외 여부 |
| 운영 리포트가 비어 있음 | `lab_gpu_busy` 수집 시작 시점, Prometheus 보관 기간, 기록 규칙 로드 여부 |
| 학생별 GPU-hour가 0 | `lab:gpu_user_active:max` 규칙, 프로세스 수집기 success, `user` 라벨 |
| 재부팅 후 컨테이너 미기동 | Container Manager 상태, restart 정책, 볼륨 권한, 이미지 오류 |

## 자주 쓰는 확인 명령

NAS에서:

```sh
cd /volume1/docker/gpu-lab/nas
docker compose ps
docker compose logs --tail=100 portal librebooking prometheus
curl -s localhost:8000/api/health
curl -s localhost:8000/metrics | head
curl -s 'localhost:9090/api/v1/targets?state=active' | head -c 400
curl -s --get localhost:9090/api/v1/query --data-urlencode 'query=lab_gpu_process_collector_success'
curl -s --get localhost:9090/api/v1/query --data-urlencode 'query=count_over_time(lab_gpu_busy[1h])'
```

GPU 호스트에서:

```sh
nvidia-smi
systemctl status prometheus-node-exporter lab-gpu-process.timer
sudo docker compose -f gpu-server/docker-compose.yml ps
curl -s localhost:9400/metrics | grep DCGM_FI_DEV_GPU_UTIL | head
curl -s localhost:9100/metrics | grep lab_gpu_process
journalctl -u lab-gpu-process.service -n 30
```

## 상태별 해석

- **UNKNOWN이 사라지지 않음**: exporter는 살아 있어도 표본이 부족하면 UNKNOWN입니다. 30초 scrape 기준 10분에 최소 18개 GPU 표본, 90초 이내 프로세스 수집이 필요합니다. 재시작 직후에는 정상적으로 몇 분간 UNKNOWN입니다.
- **예약이 있는데 화면에 안 보임**: 승인 대기(`requiresApproval`) 예약은 제외됩니다. `config/resources.yml`의 `librebooking_resource_id`가 실제 자원 ID와 같은지 확인합니다.
- **실사용자가 "확인 필요"**: 프로세스 수집기가 실패했거나(`lab_gpu_process_collector_success=0`) MIG처럼 매핑되지 않은 장치입니다. 수집기는 UID만 읽으며 실패 시 사용자 정보를 비웁니다.
- **예약자와 이름이 다름**: `config/resources.yml`의 `librebooking_user_id ↔ linux_username` 매핑을 확인합니다.
- **CONFLICT가 계속 표시됨**: 같은 시간에 두 예약이 겹쳐 있거나 예약자와 다른 사용자가 동시에 실행 중입니다. 시스템은 조정하지 않습니다. 사용자 간 확인 후 예약을 정리합니다.

## 복구 후 확인

1. Prometheus `/targets`에서 해당 job이 UP.
2. 포털 화면에서 오류 배너가 사라지고 상태가 OFFLINE/UNKNOWN에서 실제 상태로 전환.
3. `/api/reports?days=7`의 수집률이 다시 올라가는지 확인(중단 구간은 수집률로 드러나며 0%로 채우지 않습니다).
4. 조치 내용, 시각, 원인, 재발 방지책을 운영 기록에 남깁니다.

## 장애 훈련(troubleshooting drill)

원문 16.3의 권장 과제입니다. 운영 데이터에 영향이 없는 순서로 진행하고 결과를 문서화합니다.

1. GPU 호스트에서 `sudo systemctl stop prometheus-node-exporter` → 포털에서 CPU/RAM이 사라지고 5분 뒤 `GPUExporterDown` 알림이 뜨는지 확인 → 재시작 후 복구 확인.
2. DCGM 컨테이너 정지 → 해당 GPU가 OFFLINE으로 바뀌는지, FREE로 오판하지 않는지 확인 → 복구.
3. 프로세스 수집기 타이머 정지 → Borrowable 표시가 UNKNOWN으로 보수적으로 바뀌는지 확인 → 복구.
4. 포털 컨테이너 정지 → 통계 수집이 멈추고 수집률이 낮아지는지 확인 → 복구.
5. NAS 재부팅 → 모든 컨테이너 자동 기동과 대시보드 복구 확인.

각 단계에서 "무엇이 화면에 어떻게 보였는지"와 복구 소요 시간을 기록합니다.

[운영·백업](operations.md) · [배포](deployment.md) · [시스템 구조](architecture.md)
