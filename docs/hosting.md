# 공유와 외부 접속

연구실 밖에서도 접속할 수 있게 만드는 방법과, 그 전에 반드시 확인할 것들입니다. 계정은 포털이 직접 관리하므로(가입 코드 + 비밀번호 + 세션 쿠키), 외부에 열 때는 **HTTPS가 필수**입니다.

## 1. 저장소 공유 (코드)

```sh
git init                      # 이미 되어 있다면 생략
git add .
git commit -m "GPU lab reservation portal"
git remote add origin https://github.com/<계정>/<저장소>.git
git push -u origin main
```

`.gitignore`가 다음을 제외합니다. **커밋 전에 한 번 확인하세요.**

| 제외 대상 | 이유 |
|---|---|
| `nas/.env` | 가입 코드, Grafana 비밀번호 |
| `data/` | 계정 DB(`portal.sqlite3`), 가입 코드 파일 |
| `.venv/`, `artifacts/` | 재생성 가능 |

공개 저장소에 서버 IP를 넣고 싶지 않다면 private 저장소를 쓰거나, `config/resources.yml`과 `nas/prometheus/prometheus.yml`의 IP를 커밋 전에 예시 값으로 되돌립니다. 비밀번호 해시가 들어 있는 `data/portal.sqlite3`는 어떤 경우에도 커밋하지 마세요.

## 2. 서비스 접속 방법

| 방법 | 접근 범위 | 준비물 | 권장 |
|---|---|---|---|
| 교내/연구실 내부망 | 같은 네트워크 | 없음 | 기본값 |
| VPN | 학교 VPN 사용자 | 학교 VPN 계정 | 가장 안전 |
| Cloudflare Tunnel | 인터넷 전체 | Cloudflare 계정, 도메인 | 외부 접속이 꼭 필요할 때 |
| 클라우드 VM + 도메인 | 인터넷 전체 | VM, 도메인, 인증서 | 운영 부담 큼 |

GPU 서버와 NAS의 관리 포트(9090, 9100, 9400, DSM 5000/5001)는 **어떤 경우에도 외부에 열지 않습니다.** 외부에 여는 것은 포털(8000) 하나로 제한하세요.

### 내부망 (기본)

`nas/.env`에서 `NAS_BIND_IP`를 NAS의 내부망 IP로 지정하고 DSM 방화벽에서 연구실/교내 대역만 허용합니다. 학생은 `http://<NAS_IP>:8000`으로 접속합니다.

### Cloudflare Tunnel (외부 접속)

포트를 열지 않고 HTTPS 도메인을 얻는 가장 간단한 방법입니다.

```sh
# NAS 또는 포털이 도는 호스트에서
cloudflared tunnel login
cloudflared tunnel create gpu-lab
cloudflared tunnel route dns gpu-lab gpu.example.com
cloudflared tunnel run --url http://127.0.0.1:8000 gpu-lab
```

터널을 쓰면 브라우저는 HTTPS로 접속하므로 `nas/.env`에 다음을 설정하고 포털을 재시작합니다.

```sh
PORTAL_SECURE_COOKIES=1
```

`cloudflared`를 상시 실행하려면 `cloudflared service install` 또는 compose 서비스로 등록합니다.

### reverse proxy (Nginx 등)

이미 인증서가 있다면 443 → `127.0.0.1:8000`으로 프록시하고 `PORTAL_SECURE_COOKIES=1`을 설정합니다. `X-Forwarded-*` 헤더를 전달하고, 포털 컨테이너는 내부망에만 바인딩합니다.

## 3. 외부에 열기 전 점검표

- [ ] HTTPS로만 접속되는가 (http 접속은 리다이렉트).
- [ ] `PORTAL_SECURE_COOKIES=1`로 세션 쿠키가 HTTPS 전용인가.
- [ ] `PORTAL_SIGNUP_CODE`를 연구실 인원에게만 공유했는가. 유출되면 값을 바꾸고 재시작(기존 계정은 유지).
- [ ] 첫 계정(관리자)을 본인이 만들었는가. 관리자만 다른 사람의 예약을 수정·취소할 수 있습니다.
- [ ] Prometheus(9090), exporter(9100/9400), DSM 포트가 외부에서 닫혀 있는가.
- [ ] `data/portal.sqlite3` 백업 주기를 정했는가([운영](operations.md)).
- [ ] 저장소에 `.env`, `data/`가 올라가지 않았는가 (`git status`로 확인).

## 4. 계정 운영

- 가입은 **가입 코드**로만 가능합니다. 코드는 환경변수 `PORTAL_SIGNUP_CODE`, 없으면 `data/signup_code.txt`에서 읽습니다. 둘 다 없으면 시작할 때 무작위로 만들어 로그에 한 번 출력합니다.
- 첫 가입 계정이 관리자입니다. 관리자는 모든 예약을 수정·취소할 수 있습니다.
- 비밀번호는 PBKDF2-SHA256(240,000회)으로 해시해 저장하며 평문은 어디에도 남기지 않습니다. 로그인 실패가 5분 내 5회를 넘으면 해당 계정·IP 조합을 5분간 잠급니다.
- 세션은 14일간 유지되는 HttpOnly 쿠키이며, 서버에는 토큰의 해시만 저장합니다.
- 비밀번호 재설정 화면은 아직 없습니다. 잊어버린 경우 관리자가 `data/portal.sqlite3`에서 해당 계정을 지우고 다시 가입하게 합니다.

[시스템 구조](architecture.md) · [배포](deployment.md) · [운영·백업](operations.md) · [장애 대응](troubleshooting.md)
