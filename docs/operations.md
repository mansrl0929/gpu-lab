# 운영·백업·장애 대응

## 정상 운영

학생은 포털 하나를 북마크합니다. 각자 가입 코드로 계정을 만들고, 예약 생성·수정·취소를 직접 합니다. 본인 예약만 수정할 수 있고 관리자는 전체를 조정할 수 있습니다. Grafana는 학생용 Viewer 계정을 발급하고 관리자 계정은 별도로 둡니다. 최초 운영 기간에는 quota/승인제를 두지 않고 사용 충돌을 대화로 해결합니다.

## 계정 관리

가입 코드(`PORTAL_SIGNUP_CODE`)를 아는 사람만 계정을 만들 수 있습니다. 코드가 외부로 알려지면 값을 바꾸고 포털만 재시작하면 됩니다(기존 계정·예약 유지). 졸업·전출자는 관리자가 `portal.sqlite3`의 `users`에서 삭제하고, 남은 예약은 관리자 권한으로 정리합니다. 비밀번호 재설정 화면은 아직 없으므로 분실 시 같은 방법으로 계정을 지우고 재가입합니다. 비밀번호는 해시로만 저장되며 관리자도 원문을 볼 수 없습니다.

GPU 증설/교체 시 LibreBooking resource와 `config/resources.yml`을 같이 수정하고 Prometheus server 라벨을 유지합니다. 자원 매핑은 포털 시작 시 읽으므로 컨테이너를 재시작해야 반영됩니다. 임의 GPU UUID를 resource ID로 하드코딩하지 않습니다.

## 운영 리포트

포털의 **운영 리포트**(`#reports`)는 최근 7일·30일의 GPU별 평균 사용률, 실사용/유휴 시간, 예약 시간 대비 사용 비율, 학생별 GPU-hour, 멀티 GPU 예약 비율, 서버별 CPU/RAM 포화 시간대를 보여줍니다. 원문 17.1의 확장 통계에 해당하며 **서버 증설·재배치 판단 자료이지 학생 평가 자료가 아닙니다.**

집계 구간은 Prometheus 보관 기간(기본 30일)과 포털 연동 시작 시점 이후로 제한됩니다. 실사용 GPU-hour는 프로세스 수집기가 확인한 Linux 사용자 기준이며 대여 사용도 그 사용자 시간으로 잡힙니다. 수집이 끊긴 구간은 유휴 0%로 채우지 않고 수집률로 드러냅니다. 수집률이 낮은 기간의 수치로 증설을 결정하지 마세요.

## 알림

예약자≠실사용자 알림은 현재 스냅샷 기준이며, 포털 우측 상단 알림에서 대여 사용, 사용 조정 필요, 오프라인, 확인 필요, **예약 종료 후 사용 지속**(예약이 끝난 뒤 2시간 안에 같은 사용자의 작업이 계속 실행 중)을 표시합니다. 온도/XID/5분 이상 exporter down, 30분 이상 오프라인, 10분 이상 대여 사용, 포털 수집 중단은 Prometheus rules가 생성합니다. 현재 스택은 **외부 이메일·Slack 수신처를 구성하지 않습니다**. 외부 알림이 필요하면 Alertmanager 또는 Grafana contact point를 운영자가 실제 수신처와 함께 추가합니다. LibreBooking 예약 메일 알림에는 별도 SMTP 설정이 필요합니다.

## 백업

우선순위: **포털 DB(`portal.sqlite3` — 계정·예약)** > Grafana volume/provisioning > 배포 설정(`nas/.env`) > Prometheus TSDB. LibreBooking을 함께 쓰면 MariaDB 논리 덤프가 최우선입니다.

```sh
# 포털 계정·예약 백업 (컨테이너를 멈추지 않고 파일 사본을 만듭니다)
mkdir -p backups && umask 077
docker compose cp portal:/app/data/portal.sqlite3 "backups/portal-$(date +%Y%m%d-%H%M%S).sqlite3"
```

쓰기 도중 복사를 피하려면 조용한 시간대에 받거나 `docker compose stop portal` 후 복사합니다. 복사본에는 비밀번호 해시와 예약 내용이 들어 있으므로 권한을 제한하고 NAS Hyper Backup의 암호화된 저장소에 보관합니다. 복구는 반대로 `docker compose cp <파일> portal:/app/data/portal.sqlite3` 후 `docker compose up -d portal`입니다.

LibreBooking을 함께 쓰는 경우에만 아래 MariaDB 덤프가 필요합니다. `nas/`에서 Linux NAS shell을 사용하고, DB 암호는 명령 출력이나 파일 이름에 넣지 않습니다.

```sh
umask 077
mkdir -p backups
backup_stamp=$(date +%Y%m%d-%H%M%S)
docker compose exec -T librebooking-db sh -c 'exec mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --single-transaction --routines --events librebooking' > "backups/librebooking-$backup_stamp.sql"
test -s "backups/librebooking-$backup_stamp.sql"
```

명령 실패 여부(exit code)도 확인합니다. 실행 이미지에 `mariadb-dump`만 있으면 해당 명령으로 바꿉니다. 논리 덤프와 `.env`는 개인 정보/비밀을 포함하므로 권한을 제한하고 NAS Hyper Backup의 암호화된 저장소에 보관합니다.

named volume은 `docker volume inspect gpu-lab_librebooking-app` 등으로 실제 경로를 확인하여 NAS 백업 정책에 포함합니다. LibreBooking app/config·업로드와 Grafana DB를 파일 단위로 백업할 때는 해당 서비스를 정지하거나 일관된 snapshot을 사용합니다. Prometheus 원시 데이터 디렉터리는 쓰기 중 단순 복사보다 정상 정지 후 snapshot을 사용합니다. 보관은 30일/10GB로 제한되어 있습니다.

## 복구 연습

실제 운영 데이터에 덮어쓰지 말고 다른 프로젝트 이름, 별도 volume, 별도 포트의 테스트 스택에서 복구합니다. 포털만 쓰는 구성이라면 백업한 `portal.sqlite3`를 테스트 스택에 넣고 로그인·예약 목록·권한이 그대로인지 확인하면 됩니다.

LibreBooking을 함께 쓰는 경우:

1. 동일한 검증 이미지 태그로 MariaDB와 LibreBooking 컨테이너를 준비합니다.
2. 설치된 schema가 아닌 백업 시점 DB로 복원할 수 있도록 테스트 DB를 준비합니다.
3. `docker compose exec -T librebooking-db sh -c 'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" librebooking' < backups/선택한백업.sql`을 테스트 스택에서 실행합니다.
4. 대응하는 config/uploads와 Grafana 데이터를 복원합니다.
5. 학생·자원·단일/멀티 GPU 예약·권한·날짜가 동일한지 확인합니다.
6. 테스트 계정으로 새 예약 생성/취소를 확인한 뒤 복구 결과와 백업 날짜를 기록합니다.

## 장애 대응

증상별 1차 점검표, 확인 명령, 상태 해석, 장애 훈련(drill) 절차는 [장애 대응](troubleshooting.md)으로 옮겼습니다.

포털은 upstream 장애를 실제 화면에 표시하며 성공한 과거 데이터로 FREE를 판정하지 않습니다. 브라우저→포털 자체 연결이 끊긴 경우 화면에 마지막 수신 정보라는 경고가 나타납니다.

## 업데이트

변경 전 DB 덤프와 config/Grafana snapshot을 만듭니다. 테스트 스택에서 이미지와 실제 LibreBooking API 응답을 확인한 후 태그를 고정하고 운영에 반영합니다. 스키마 migration 이후에는 이미지 태그만 되돌려서는 복구되지 않을 수 있으므로 DB도 대응 시점으로 복원해야 합니다.

[시스템 구조](architecture.md) · [배포](deployment.md) · [장애 대응](troubleshooting.md) · [인수 기준](acceptance.md)
