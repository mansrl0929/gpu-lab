# iGDSL 설치 실행서

이 연구실 장비에 맞춰 작성한 실제 실행 순서입니다. 모든 명령은 복사해서 붙여넣으면 됩니다.

| 장비 | 주소 | OS | GPU |
|---|---|---|---|
| iGDSL-Aurora | `10.174.52.127` | Ubuntu 26.04 | RTX PRO 6000 Blackwell × 4 (96 GB) |
| iGDSL-Polaris | `10.174.52.130` | Ubuntu 24.04 | RTX A6000 × 2 (48 GB) |

포털은 **Aurora에서 실행**하는 것을 기준으로 적었습니다(NAS나 다른 서버에서 돌려도 동일하며, `prometheus.yml`의 주소만 맞으면 됩니다).

---

## 1단계 — 저장소 올리기 (Windows PC에서 1회)

GitHub에서 **private** 저장소를 하나 만든 뒤(README 체크 해제), 로컬에서:

```powershell
cd D:\web
git remote add origin https://github.com/<계정>/gpu-lab.git
git branch -M main
git push -u origin main
```

`nas/.env`, `data/`(계정 DB·가입 코드)는 `.gitignore`로 빠집니다. 커밋 전에 `git status`로 확인하세요.

---

## 2단계 — Aurora에 포털 올리기

```sh
ssh munkiyeong@10.174.52.127

# 도커가 없으면
sudo apt update && sudo apt install -y docker.io docker-compose-v2 git
sudo usermod -aG docker $USER && newgrp docker

git clone https://github.com/<계정>/gpu-lab.git ~/gpu-lab
cd ~/gpu-lab/nas
cp .env.example .env
nano .env
```

`.env`에서 최소한 이 네 줄을 바꿉니다.

```sh
NAS_BIND_IP=0.0.0.0              # 연구실 안에서 10.174.52.127:8000 으로 접속
NAS_PUBLIC_HOST=10.174.52.127
PORTAL_SIGNUP_CODE=iGDSL1234
GRAFANA_ADMIN_PASSWORD=<직접 정한 비밀번호>
```

```sh
docker compose config --quiet     # 문법 확인
docker compose up -d --build
docker compose ps
```

브라우저에서 **http://10.174.52.127:8000** → 회원가입 화면. 가입 코드 `iGDSL1234`로 **먼저 가입한 계정이 관리자**입니다.

---

## 3단계 — 두 서버에 수집기 설치

Aurora와 Polaris **각각**에서 실행합니다. 포털이 Aurora에 있으므로 인자로 `10.174.52.127`을 줍니다.

```sh
# Aurora
cd ~/gpu-lab && sudo bash scripts/install_gpu_host.sh 10.174.52.127

# Polaris
ssh kiyeong@10.174.52.130
git clone https://github.com/<계정>/gpu-lab.git ~/gpu-lab
cd ~/gpu-lab && sudo bash scripts/install_gpu_host.sh 10.174.52.127
```

스크립트가 하는 일: `node_exporter`(9100) 설치, GPU 프로세스 수집기(30초 타이머) 등록, DCGM Exporter(9400) 컨테이너 기동, 9100/9400을 포털 서버에서만 접근하도록 방화벽 설정. **프로세스를 종료하거나 GPU 권한을 바꾸지 않습니다.**

끝나면 Aurora에서 확인:

```sh
curl -s http://10.174.52.130:9400/metrics | grep -m1 DCGM_FI_DEV_GPU_UTIL
docker compose -f ~/gpu-lab/nas/docker-compose.yml logs --tail=20 prometheus
```

10분쯤 지나면 포털의 GPU 카드가 "모니터링 미연동"에서 실제 사용률(현재 A6000 2장은 100% 사용 중)로 바뀝니다. 표본이 쌓이기 전에는 "확인 필요"로 보수적으로 표시되는 것이 정상입니다.

---

## 4단계 — 연구실 밖에서 접속

포트를 열지 않고 HTTPS 주소를 얻는 방법입니다.

### 빠르게 (임시 주소, 계정 불필요)

```sh
# Aurora에서
docker run --rm --network host cloudflare/cloudflared:2025.8.1 tunnel --url http://localhost:8000
```

출력되는 `https://xxxx-xxxx.trycloudflare.com` 주소로 어디서나 접속됩니다. 명령을 끄면 주소도 사라집니다. **임시 주소로 로그인할 때는 아래 고정 주소 설정을 먼저 하는 편이 안전합니다**(쿠키가 HTTPS 전용이 아니면 중간에서 가로챌 수 있습니다).

### 고정 주소 (권장)

1. Cloudflare 계정에 도메인을 연결하고 Zero Trust → Networks → Tunnels에서 터널을 만듭니다.
2. Public hostname을 `gpu.<도메인>` → `http://portal:8000`으로 지정합니다.
3. 발급된 토큰을 `nas/.env`에 넣고 쿠키를 HTTPS 전용으로 바꿉니다.

```sh
TUNNEL_TOKEN=<복사한 토큰>
PORTAL_SECURE_COOKIES=1
```

```sh
docker compose --profile tunnel up -d
docker compose restart portal
```

이제 `https://gpu.<도메인>`으로 연구실 밖에서도 접속합니다. 학교 VPN만 쓸 거라면 이 단계를 건너뛰고 `NAS_BIND_IP`만 내부망 IP로 두면 됩니다.

> 외부에 여는 것은 포털(8000) 하나뿐입니다. Prometheus(9090)·exporter(9100/9400)·SSH는 절대 외부에 열지 마세요.

---

## 5단계 — 연구실 인원 안내

전달할 내용은 두 가지입니다.

```
주소: https://gpu.<도메인>   (또는 http://10.174.52.127:8000)
가입 코드: iGDSL1234
```

각자 아이디·이름·비밀번호를 정해 가입하면 바로 예약할 수 있습니다. 예약은 우선 사용권이며, 예약하지 않아도 GPU는 그대로 쓸 수 있고 시스템이 작업을 끊지 않습니다.

---

## 갱신 주기

| 항목 | 주기 |
|---|---|
| 화면 자동 갱신 | 10초 |
| GPU 메트릭 수집 | 10초 |
| 서버 CPU/RAM/Disk | 15초 |
| GPU 프로세스 사용자 | 30초 |
| 예약 대비 사용 기록 | 30초 |

## 문제가 생기면

- GPU가 계속 "오프라인" → Polaris/Aurora에서 `curl localhost:9400/metrics`, 포털 서버에서 `curl 10.174.52.130:9400/metrics`.
- 실사용자가 "확인 필요" → `systemctl status lab-gpu-process.timer`, `journalctl -u lab-gpu-process.service -n 30`.
- 그 밖의 증상은 [장애 대응](troubleshooting.md).

## 업데이트

```sh
cd ~/gpu-lab && git pull
cd nas && docker compose up -d --build
```

계정과 예약은 `portal` 볼륨에 남아 업데이트해도 유지됩니다. 백업은 [운영·백업](operations.md)을 참고하세요.
