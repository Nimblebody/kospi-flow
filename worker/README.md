# 정시 실행용 Cloudflare Worker

GitHub 예약(cron)이 제때 발사되지 않아 뺐다. 대신 이 Worker 가 정시에
`workflow_dispatch` 를 때린다. 앱의 '지금 갱신' 버튼도 같은 곳을 부른다.

- 코드: `scheduler.js` (이 저장소가 원본. 대시보드에서 고쳤으면 여기도 맞춰둘 것)
- 크론: `30 7 * * 1-5` (UTC) = 평일 16:30 KST
- 중복 방지: 최근 30분 안에 실행됐거나 지금 도는 중이면 건너뛴다.
  GitHub 실행 기록을 직접 보므로 KV 같은 저장소가 필요 없다.

## 필요한 값

| 이름 | 종류 | 설명 |
|---|---|---|
| `GITHUB_TOKEN` | Secret | fine-grained PAT. 이 저장소의 Actions 읽기/쓰기만 |
| `TRIGGER_KEY` | Secret | 앱이 부를 때 붙이는 열쇠. 안 넣으면 열쇠 없이 열림 |

`TRIGGER_KEY` 는 앱(공개 페이지)에 박히므로 진짜 비밀은 아니다. 지나가는
스캐너를 거르는 용도고, 실제 남용은 위의 30분 규칙이 막는다.

## 확인

    curl https://<워커주소>/health
    curl "https://<워커주소>/?key=<TRIGGER_KEY>"

두 번째를 연달아 부르면 `skipped: true` 가 나와야 정상이다.
