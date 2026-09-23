# 처음 설치하기 — 단계별 따라하기

git이나 리눅스를 몰라도 그대로 따라 할 수 있게 적었습니다. **어디서 입력하는지**를 각 단계 제목에 적어 두었으니, 위치만 맞으면 나머지는 복사·붙여넣기입니다.

> PowerShell에 붙여넣기: 마우스 **오른쪽 클릭** 한 번이면 붙여집니다. 명령은 한 줄씩 넣고 Enter를 누르세요.
> 비밀번호를 입력할 때는 화면에 아무것도 안 보이는 게 정상입니다. 그대로 치고 Enter.

전체 그림: **내 PC → GitHub에 코드 올림 → Aurora 서버가 그 코드를 받아 실행 → 모두가 접속.**

---

## 0단계 — GitHub 계정 (내 PC, 웹브라우저)

이미 있으면 건너뜁니다. 없으면 <https://github.com/signup> 에서 만듭니다. 이메일 인증까지 끝내세요.

---

## 1단계 — 빈 저장소 만들기 (내 PC, 웹브라우저)

1. <https://github.com/new> 를 엽니다.
2. **Repository name**에 `gpu-lab` 을 적습니다.
3. **Public**을 고릅니다. (서버에서 받을 때 로그인이 필요 없어 가장 간단합니다. 비밀번호·가입 코드는 애초에 올라가지 않습니다. 굳이 감추고 싶으면 아래 *부록 A* 참고)
4. 아래 **Add a README file / .gitignore / license 는 모두 체크하지 않습니다.**
5. 초록색 **Create repository** 버튼.
6. 다음 화면 주소창의 `https://github.com/<내계정>/gpu-lab` 을 복사해 둡니다.

---

## 2단계 — 코드 올리기 (내 PC, PowerShell)

시작 메뉴에서 `powershell` 을 검색해 **Windows PowerShell**을 엽니다. 아래를 한 줄씩:

```powershell
cd D:\web
git remote add origin https://github.com/<내계정>/gpu-lab.git
git branch -M main
git push -u origin main
```

`<내계정>` 자리에 본인 GitHub 아이디를 넣으세요.

- 브라우저 창이 뜨면서 GitHub 로그인을 요구하면 **Sign in with your browser**를 눌러 로그인합니다. 한 번만 하면 됩니다.
- `Enumerating objects... done.` 이 나오면 성공입니다.
- 1단계의 GitHub 페이지를 새로고침하면 파일들이 보입니다.

> `git remote add origin` 에서 *remote origin already exists* 가 나오면 이미 연결된 것입니다.
> `git remote set-url origin https://github.com/<내계정>/gpu-lab.git` 으로 주소만 바꾸고 계속하세요.

---

## 3단계 — Aurora 서버에 프로그램 올리기 (PowerShell → 서버)

같은 PowerShell 창에서 서버에 접속합니다.

```powershell
ssh munkiyeong@10.174.52.127
```

비밀번호를 넣고 들어가면 프롬프트가 `munkiyeong@iGDSL-Aurora:~$` 로 바뀝니다. **여기서부터는 서버 안입니다.**

```sh
# 3-1. 필요한 프로그램 (이미 있으면 그냥 넘어갑니다)
sudo apt update
sudo apt install -y git docker.io docker-compose-v2

# 3-2. 내 계정이 docker를 쓸 수 있게 (한 번만)
sudo usermod -aG docker $USER
newgrp docker

# 3-3. 코드 받기
git clone https://github.com/<내계정>/gpu-lab.git ~/gpu-lab
cd ~/gpu-lab

# 3-4. 설정 채우기 (편집기 없이 자동)
bash scripts/setup_env.sh 10.174.52.127 iGDSL1234

# 3-5. 실행 (처음에는 5~10분 걸립니다)
cd nas
docker compose up -d --build
docker compose ps
```

3-4를 실행하면 서버 주소·가입 코드가 채워지고 Grafana 비밀번호가 자동 생성되어 화면에 나옵니다. 그 값을 메모해 두세요.

`portal`, `prometheus`, `grafana`, `homepage` 가 모두 `running` 이면 성공입니다.

---

## 4단계 — 첫 계정 만들기 (내 PC, 웹브라우저)

주소창에 **http://10.174.52.127:8000** 을 칩니다. 회원가입 화면이 나옵니다.

| 칸 | 입력 |
|---|---|
| 아이디 | 영문 소문자 (예: `munkiyeong`) |
| 사용자 이름 | 화면에 보일 이름 (예: `문기영`) |
| 비밀번호 | 8자 이상, 본인만 아는 값 |
| 연구실 가입 코드 | `iGDSL1234` |

가입 코드만 알면 누구나 바로 가입합니다. 맨 처음 가입한 계정에는 다른 사람 예약을 정리할 수 있는 권한이 함께 붙으니, 관리하실 분이 먼저 가입하세요.

이 시점에는 GPU 카드가 "모니터링 미연동"으로 보입니다. 정상입니다. 5단계를 하면 실제 사용률이 들어옵니다.

---

## 5단계 — 두 서버에서 GPU 수치 읽어오기 (서버)

### 5-1. Aurora (지금 접속해 있는 서버)

```sh
cd ~/gpu-lab
sudo bash scripts/install_gpu_host.sh 10.174.52.127
```

마지막에 `OK: GPU 사용률이 수집되고 있습니다.` 가 나오면 성공입니다.

### 5-2. Polaris

```sh
exit                      # Aurora에서 나오기
ssh kiyeong@10.174.52.130 # 비밀번호 입력
```

```sh
sudo apt update && sudo apt install -y git
git clone https://github.com/<내계정>/gpu-lab.git ~/gpu-lab
cd ~/gpu-lab
sudo bash scripts/install_gpu_host.sh 10.174.52.127
exit
```

이 스크립트는 `nvidia-smi` 값을 10초마다 읽어서 내보내기만 합니다. **작업을 멈추거나 GPU 설정을 바꾸지 않습니다.**

2~3분 뒤 포털을 새로고침하면 GPU 카드에 실제 사용률·VRAM·온도와 지금 돌리고 있는 사람의 계정이 나타납니다. 처음 10분 정도는 표본이 모자라 "확인 필요"로 조심스럽게 표시될 수 있습니다.

---

## 6단계 — 연구실 밖에서 접속 (Aurora 서버)

```powershell
ssh munkiyeong@10.174.52.127
```

```sh
cd ~/gpu-lab/nas
docker compose --profile quicktunnel up -d
sleep 20
bash ../scripts/tunnel_url.sh
```

`외부 접속 주소: https://○○○-○○○-○○○.trycloudflare.com` 과 `확인: 정상 동작합니다.` 가 나오면 성공입니다. 전화 데이터로도 열립니다.

이 주소는 **quicktunnel 컨테이너가 다시 뜰 때마다 바뀝니다.** 주소를 잊었거나 접속이 안 되면 언제든 `bash ~/gpu-lab/scripts/tunnel_url.sh` 로 현재 주소를 확인하세요.

```sh
# 주소를 받은 뒤, 쿠키를 HTTPS 전용으로 바꿔줍니다 (보안)
sed -i 's|^PORTAL_SECURE_COOKIES=.*|PORTAL_SECURE_COOKIES=1|' .env
docker compose up -d portal
```

> 이 임시 주소는 컨테이너를 다시 만들면 바뀝니다. **항상 같은 주소**를 쓰려면 아래 6-2로 넘어가세요.

### 6-2. 고정 주소 만들기 (권장, 무료)

Tailscale Funnel을 쓰면 서버를 재시작해도 바뀌지 않는 HTTPS 주소가 생깁니다. **접속하는 사람은 아무것도 설치하지 않아도 됩니다.**

```sh
cd ~/gpu-lab
sudo bash scripts/setup_tailscale.sh 8000 iGDSL-GPU
```

1. 중간에 주소가 하나 표시됩니다. 브라우저에서 열어 **구글/깃허브로 로그인**하면 됩니다(무료 계정).
2. Funnel을 못 켰다는 안내가 나오면, 화면에 찍힌 링크를 열어 한 번 허용한 뒤 `sudo tailscale funnel --bg 8000` 을 다시 실행합니다.
3. **주소는 나왔는데 밖에서 "사이트에 연결할 수 없음"이 뜨면** HTTPS 인증서가 꺼져 있는 것입니다. <https://login.tailscale.com/admin/dns> 에서 **HTTPS Certificates**를 Enable한 뒤 서버에서 `sudo tailscale cert <주소>` → `sudo tailscale funnel --bg 8000` 을 실행하세요. 공개 DNS 등록까지 1~2분 걸립니다.
4. 마지막에 `외부 접속 주소: https://igdsl-gpu.____.ts.net` 이 나옵니다. 이 주소가 고정 주소입니다.

주소 앞부분은 원하는 이름(`iGDSL-GPU` → `igdsl-gpu`)으로 정해지고, 뒷부분은 Tailscale이 계정에 붙이는 이름입니다. 완전히 원하는 주소(`gpu.igdsl.kr` 같은)를 쓰려면 도메인을 구매해 부록 B로 진행하세요.

고정 주소를 쓰기 시작하면 임시 터널은 꺼도 됩니다.

```sh
cd ~/gpu-lab/nas && docker compose --profile quicktunnel down
```

---

## 7단계 — 연구실에 공지

두 가지만 알려주면 됩니다.

```
GPU 예약: <위에서 받은 주소>
가입 코드: iGDSL1234
```

각자 가입해서 바로 예약하면 됩니다. 예약은 "이 시간에 내가 먼저 쓸게요"라는 표시이고, 예약이 없어도 GPU는 평소처럼 쓸 수 있습니다. 시스템이 남의 작업을 끄지 않습니다.

---

## 계정을 전부 지우고 다시 시작하려면

```sh
ssh munkiyeong@10.174.52.127
cd ~/gpu-lab && git pull
bash scripts/reset_accounts.sh --yes
```

계정·세션·예약이 모두 지워지고, 지우기 전 사본이 `nas/backups/`에 남습니다. 가입 코드와 수집된 사용량 기록은 그대로입니다. 이후 브라우저에서 새로 가입하면 됩니다.

## 나중에 코드를 고쳤을 때

```powershell
# 내 PC
cd D:\web
git add -A
git commit -m "변경 내용 한 줄"
git push
```

```sh
# 서버
ssh munkiyeong@10.174.52.127
cd ~/gpu-lab && git pull
cd nas && docker compose up -d --build
```

계정과 예약은 그대로 유지됩니다.

---

## 자주 막히는 곳

| 증상 | 해결 |
|---|---|
| `git push` 에서 인증 실패 | 브라우저 로그인 창을 닫았을 수 있습니다. 명령을 다시 실행하세요. |
| `permission denied while trying to connect to the Docker daemon` | `newgrp docker` 를 안 했거나 재접속이 필요합니다. `exit` 후 다시 ssh 접속. |
| 포털이 안 열림 | 서버에서 `cd ~/gpu-lab/nas && docker compose ps`, `docker compose logs portal | tail -30` |
| GPU가 계속 "모니터링 미연동" | 서버에서 `curl -s localhost:9100/metrics | grep DCGM_FI_DEV_GPU_UTIL` 이 나오는지 확인 |
| 실사용자가 "확인 필요" | `systemctl status lab-gpu-process.timer`, `journalctl -u lab-gpu-process.service -n 30` |
| 가입 코드가 안 먹힘 | 대소문자를 구분합니다. `iGDSL1234` (맨 앞 소문자 i) |
| nano에서 글자가 안 고쳐짐 | 편집기를 쓰지 마세요. `Ctrl+X`로 나온 뒤 `bash scripts/setup_env.sh 10.174.52.127 iGDSL1234` |
| `.env` 값이 `CHANGE_ME` 그대로 | 위와 같은 명령으로 다시 채우고 `docker compose up -d` |
| 외부 주소가 `Error 1033` / 530 | 임시 터널 주소가 바뀐 것입니다. `bash ~/gpu-lab/scripts/tunnel_url.sh` 로 확인하거나, 6-2로 고정 주소를 만드세요. |
| 고정 주소가 안 열림 | 관리 콘솔에서 HTTPS Certificates Enable → `sudo tailscale cert <주소>` → `sudo tailscale funnel --bg 8000` |

더 자세한 증상별 점검은 [장애 대응](troubleshooting.md).

---

## 부록 A — 저장소를 비공개(Private)로 하고 싶다면

1단계에서 **Private**을 고른 경우, 서버에서 `git clone` 할 때 로그인이 필요합니다.

1. <https://github.com/settings/tokens> → **Generate new token (classic)**
2. Note에 `gpu-lab server`, Expiration은 원하는 기간, 권한은 **repo** 하나만 체크 → Generate
3. 나오는 `ghp_...` 문자열을 복사(한 번만 보입니다).
4. 서버에서 clone할 때 아이디는 GitHub 아이디, 비밀번호 자리에 이 토큰을 붙여넣습니다.

## 부록 B — 항상 같은 주소 쓰기 (도메인 필요)

1. 도메인을 Cloudflare에 연결합니다(무료 플랜 가능).
2. Cloudflare **Zero Trust → Networks → Tunnels → Create a tunnel** → 이름 `gpu-lab`.
3. **Public hostname**: `gpu.<내도메인>` → Service `HTTP` → `portal:8000`.
4. 발급된 토큰을 서버 `~/gpu-lab/nas/.env` 의 `TUNNEL_TOKEN=` 에 붙여넣고 `PORTAL_SECURE_COOKIES=1` 로 둡니다.
5. ```sh
   cd ~/gpu-lab/nas
   docker compose --profile tunnel up -d
   docker compose up -d portal
   ```

이제 `https://gpu.<내도메인>` 이 고정 주소가 됩니다.
