# 코스피 수급 지도

매일 장이 끝나면 코스피 전체를 훑어서 **어느 테마로 돈이 들어왔고, 어제 어디서 빠져나왔는지**를
정리해 폰으로 보내주는 도구입니다.

- 데이터: 한국투자증권 KIS Open API (공식 REST)
- 자동 실행: GitHub Actions (평일 16:10 속보 / 18:30 확정)
- 결과: 모바일 대시보드 + 텔레그램 알림

---

## 1. 무엇을 보여주나

| 화면 | 내용 |
|---|---|
| **요약** | 코스피·코스닥 지수, 상승/하락 종목 수, 외국인·기관·개인 순매수 |
| **테마 수급** | 테마별 외국인+기관 순매수 대금 순위. 탭하면 구성 종목이 열림 |
| **자금 이동** | 전 거래일 대비 테마별 순매수 증감으로 추정한 이동 경로 |
| **종목** | 순매수·순매도 상위, 거래가 갑자기 몰린 종목 |
| **업종** | 코스피 업종별 등락 히트맵. 탭하면 그 업종에서 수급이 잡힌 종목이 열림 |
| **미국증시** | 간밤 3대 지수·VIX·환율·미 10년물, SPDR 섹터 ETF 등락 히트맵 |

테마별 지표에는 **참여 폭(breadth)** 이 함께 나옵니다. 테마 전체가 오른 건지
한두 종목이 끌고 간 건지 구분하려고 넣었습니다.

### 이 숫자의 성격 (중요)

- **속보(16:10)** 는 한국투자증권 *매매종목 가집계* 기준입니다. 장중 추정치라
  저녁에 나오는 확정치와 다를 수 있습니다.
- **확정(18:30)** 은 같은 데이터에 종목별 확정 투자자 매매동향(개인 순매수 포함)을
  덧붙인 것입니다.
- **자금 이동**은 "어제 A테마에서 줄어든 금액"과 "오늘 B테마에서 늘어난 금액"을
  크기순으로 짝지은 **추정**입니다. 같은 돈이 실제로 옮겨갔다는 증거가 아닙니다.
- 수급은 시장에서 벌어진 일을 정리한 것이지 앞날을 알려주지 않습니다.
  매매 판단은 본인 몫입니다.

---

## 2. 먼저 화면부터 보기 (앱키 없이)

```bash
pip install -r requirements.txt
python main.py --sample --no-notify
```

`web/index.html` 이 만들어집니다. 브라우저로 열면 가짜 데이터가 들어간 대시보드가 뜹니다.
며칠치를 만들어 보면 '자금 이동' 탭까지 채워집니다.

```bash
for d in 20260824 20260825 20260826 20260827; do
  python main.py --sample --date $d --no-notify
done
```

---

## 3. 1단계 — KIS 앱키 발급

키움 REST API 는 **토큰을 발급받을 단말기(IP)를 미리 등록**해야 해서 클라우드에서 돌리기 곤란합니다.
GitHub Actions 는 실행할 때마다 IP가 바뀌기 때문에 이 프로젝트는 **한국투자증권(KIS)** 을 씁니다.
개인은 IP 등록 없이 쓸 수 있습니다.

1. 한국투자증권 계좌가 없다면 먼저 개설 (앱에서 비대면 개설 가능)
2. [KIS Developers](https://apiportal.koreainvestment.com) 접속 → 로그인
3. 본인인증 (휴대폰 또는 공동인증서)
4. **개인 → 인증키 발급** 메뉴에서 앱 등록
   - 이름: 아무거나 (예: `kospi-flow`)
   - 환경: **실전투자** 선택 (모의투자는 초당 1건 제한이라 느립니다)
5. **APP KEY**(약 36자)와 **APP SECRET**(약 180자)이 나옵니다

> ⚠️ **APP SECRET 은 발급 직후 한 번만 보여줍니다.** 바로 복사해서 안전한 곳에 두세요.
> 저에게 알려주지 마시고, 아래 GitHub Secrets 나 `.env` 파일에 직접 넣으세요.

---

## 4. 2단계 — 저장소 만들기

1. GitHub 에서 새 저장소 생성 (이름 예: `kospi-flow`)
2. 이 폴더의 파일 전부를 올립니다

```bash
cd kospi-flow
git init
git add .
git commit -m "첫 커밋"
git branch -M main
git remote add origin https://github.com/<아이디>/kospi-flow.git
git push -u origin main
```

`.gitignore` 에 `.env` 가 들어 있어서 앱키가 실수로 올라가지 않습니다.

---

## 5. 3단계 — Secrets 등록

저장소 → **Settings → Secrets and variables → Actions**

**Secrets** 탭 (`New repository secret`):

| 이름 | 값 |
|---|---|
| `KIS_APP_KEY` | 발급받은 APP KEY |
| `KIS_APP_SECRET` | 발급받은 APP SECRET |
| `TELEGRAM_BOT_TOKEN` | (선택) 텔레그램 봇 토큰 |
| `TELEGRAM_CHAT_ID` | (선택) 내 채팅 ID |

**Variables** 탭 (`New repository variable`):

| 이름 | 값 |
|---|---|
| `SITE_URL` | 대시보드 주소. 호스팅을 안 쓰면 **비워 두세요** |
| `KIS_ENV` | `prod` (기본값이라 안 넣어도 됩니다) |

---

## 6. 4단계 — 어디에 띄울지 고르기

**무료 GitHub 계정은 공개 저장소에서만 GitHub Pages 를 쓸 수 있습니다.**
비공개로 두고 싶다면 아래 B~D 중에서 고르세요.

### A. 공개 저장소 + GitHub Pages — 가장 간단, 무료

- Actions 실행시간도 무제한입니다.
- 대신 코드와 리포트가 누구나 볼 수 있게 공개됩니다. (앱키는 Secrets 라 안전)
- 설정: 저장소 → Settings → Pages → Source 를 **GitHub Actions** 로 변경
- 주소: `https://<아이디>.github.io/kospi-flow/` → 이걸 `SITE_URL` 변수에 넣으세요

### B. 비공개 저장소 + GitHub Pro (월 $4) — 비공개 유지

- Pro 로 올리면 비공개 저장소에서도 Pages 가 켜집니다.
- 무료 Actions 시간은 월 2,000분. 이 작업은 1회 1~2분이라 **월 40~80분** 이면 충분합니다.
  (걱정하시던 500MB는 실행시간이 아니라 아티팩트 *저장 용량* 이고, 이 프로젝트는 거의 안 씁니다.)
- 참고: Pro 의 Pages 사이트도 주소를 아는 사람은 볼 수 있습니다. 저장소만 비공개입니다.

### C. 비공개 저장소 + 텔레그램만 — 무료, 완전 비공개 ← 호스팅 없이 쓰려면 이것

- `SITE_URL` 변수를 **비워 두면** 워크플로가 리포트 HTML 파일 자체를 텔레그램으로 보냅니다.
- 폰에서 파일을 탭하면 대시보드가 그대로 열립니다. 서버가 아예 필요 없습니다.
- 대신 홈화면 앱 설치(PWA)는 안 됩니다.
- 설정: `.github/workflows/daily.yml` 맨 아래 Pages 관련 3개 스텝을 지우세요.

### D. 비공개 저장소 + Cloudflare Pages — 무료, 홈화면 설치까지

- 저장소는 비공개로 두고 사이트만 Cloudflare 에 올립니다. 무료 요금제로 충분합니다.
- Cloudflare 대시보드 → Workers & Pages → Create → Pages → GitHub 연결
- 빌드 명령 없음, 출력 디렉터리 `web`
- Cloudflare Access 를 걸면 비밀번호/이메일 인증도 붙일 수 있습니다.

---

## 7. 5단계 — 텔레그램 봇 (5분)

1. 텔레그램에서 [@BotFather](https://t.me/BotFather) 검색 → `/newbot`
2. 봇 이름과 아이디를 정하면 **토큰**을 줍니다 → `TELEGRAM_BOT_TOKEN`
3. 방금 만든 봇과 대화를 시작하고 아무 메시지나 보냅니다 (이걸 해야 봇이 나에게 말을 걸 수 있습니다)
4. 브라우저에서 `https://api.telegram.org/bot<토큰>/getUpdates` 접속
5. `"chat":{"id":123456789` 의 숫자가 **`TELEGRAM_CHAT_ID`** 입니다

---

## 8. 6단계 — 첫 실행

저장소 → **Actions → 코스피 수급 리포트 → Run workflow** → stage `final` 선택 → 실행.

로그에서 아래 세 줄을 확인하세요.

```
수급 NNN종목 수집, 대금 단위 추정 = 백만원
KIS 테마 마스터 NNN개 테마
코스피 2,xxx.xx ▲0.xx% ...
```

- 첫날은 '자금 이동' 탭이 비어 있습니다. **이틀째부터** 비교가 됩니다.
- 리포트 JSON 은 저장소 `web/data/` 에 매일 쌓입니다. 이 기록이 이동·연속일수 계산의 재료입니다.

이후로는 평일 16:10 / 18:30 에 알아서 돕니다.
(GitHub 크론은 혼잡하면 5~30분 늦게 시작할 수 있습니다.)

---

## 9. 폰에 앱처럼 설치하기 (A·B·D 방식)

**iPhone** — Safari 로 사이트 접속 → 공유 버튼 → **홈 화면에 추가**
(Safari 여야 합니다. Chrome 에서는 안 됩니다.)

**Android** — Chrome 으로 접속 → 메뉴 → **앱 설치** 또는 **홈 화면에 추가**

설치하면 주소창 없이 전체화면으로 뜨고, 오프라인에서도 마지막 리포트가 열립니다.

> 앱 자체 푸시 알림에 대해: 홈 화면에 설치한 웹앱은 iOS 16.4 이상·안드로이드에서
> 푸시를 받을 수 있지만, 푸시 서버(VAPID)를 따로 띄워야 합니다. 텔레그램 알림이
> 같은 일을 훨씬 적은 품으로 해주기 때문에 이 프로젝트는 텔레그램을 씁니다.

---

## 10. 내 PC 에서 돌리기

```bash
cp .env.example .env      # .env 를 열어 앱키를 채웁니다
pip install -r requirements.txt
python main.py --stage final
```

매일 자동으로 돌리려면:

- **Windows**: 작업 스케줄러 → 작업 만들기 → 트리거 매일 18:30 →
  동작 `python`, 인수 `main.py --stage final`, 시작 위치는 이 폴더
- **macOS/Linux**: `crontab -e` 에 아래 한 줄

```
30 18 * * 1-5 cd /경로/kospi-flow && /usr/bin/python3 main.py --stage final >> run.log 2>&1
```

---

## 11. 테마 사전 바꾸기

기본은 한국투자증권이 배포하는 **공식 테마 마스터**를 매주 새로 받아 씁니다
(`theme_code.mst`, 수백 개 테마).

내가 보는 테마로 직접 관리하고 싶다면:

1. `.env` 에 `SKIP_MASTER_DOWNLOAD=1`
2. `src/fallback_themes.py` 의 사전을 원하는 대로 편집

```python
FALLBACK_THEMES = {
    "내가보는반도체": ["005930", "000660", "042700"],
    "조선": ["009540", "010140", "042660"],
}
```

`config.py` 의 `MIN_THEME_MEMBERS`(기본 2)를 올리면 한 종목이 끌고 가는 테마를 더 걸러냅니다.

---

## 12. 파일 구성

```
main.py                  실행 진입점
config.py                설정 (앱키는 .env 에서 읽음)
make_sample.py           샘플 데이터 생성기
src/kis.py               KIS REST 클라이언트 (토큰 캐시·레이트리밋·재시도)
src/masters.py           종목/테마 마스터 다운로드·파싱
src/fallback_themes.py   내장 테마 사전
src/collect.py           API 호출 → 원본 스냅샷
src/overseas.py          간밤 미국 지수·매크로·섹터 수집 (KIS 해외 시세)
src/analyze.py           테마 롤업 · 자금이동 · 연속일수 · 급증 탐지
src/render.py            정적 사이트(PWA) 빌드
src/notify.py            텔레그램 알림
src/store.py             리포트 저장·과거 리포트 로딩
templates/dashboard.html 대시보드 원본 (여기만 고치면 화면이 바뀝니다)
tests/test_analyze.py    분석 로직 테스트
web/                     빌드 결과물 (배포 대상)
```

테스트: `python tests/test_analyze.py`

---

## 13. 문제 해결

| 증상 | 원인과 해결 |
|---|---|
| `KIS_APP_KEY 가 비어 있습니다` | Secrets 이름 오타. 대소문자까지 정확히 맞춰야 합니다 |
| `EGW00201` | 초당 호출 한도 초과. `config.py` 의 `KIS_RATE_LIMIT_PER_SEC` 를 낮추세요 |
| `EGW00121` / `EGW00123` | 토큰 만료. 자동 재발급하지만 반복되면 `.cache/` 를 지우세요 |
| 모의투자로 넣었더니 너무 느림 | 모의는 초당 1건. `KIS_ENV=prod` 로 바꾸세요 |
| 대금 숫자가 이상하게 큼/작음 | 로그의 `대금 단위 추정` 을 확인. 자동 추정이 빗나가면 `src/collect.py` 의 `_detect_amount_multiplier` 를 손보면 됩니다 |
| 테마가 몇 개 안 나옴 | 마스터 다운로드 실패로 내장 사전을 쓰는 중. 로그의 `테마 분류` 항목 확인 |
| '자금 이동' 이 비어 있음 | 전 거래일 리포트가 없어서입니다. 이틀 돌면 채워집니다 |
| Pages 배포가 실패함 | 무료 계정 + 비공개 저장소 조합입니다. 위 6단계의 B~D 중에서 고르세요 |
| 미국증시 탭이 비어 있음 | KIS 계정에 해외주식 시세 이용 신청이 안 돼 있을 수 있습니다. Actions 에서 **미국 시세 코드 확인** 워크플로를 손으로 실행해 로그를 보세요. 국내 리포트는 영향 없이 그대로 나갑니다. |

---

## 14. 출처

- [한국투자증권 KIS Developers](https://apiportal.koreainvestment.com)
- [koreainvestment/open-trading-api](https://github.com/koreainvestment/open-trading-api) — 엔드포인트·tr_id 기준
- 테마 마스터: `https://new.real.download.dws.co.kr/common/master/theme_code.mst.zip`
