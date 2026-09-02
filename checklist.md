# 체크리스트 · 테마 대표성 지표

목표. 한 종목이 끌고 가는 테마와 표본이 얇은 테마를 화면 상단에서 걸러낸다.

- [x] 1. `config.py` 에 `THEME_MAX_TOP1_SHARE`, `THEME_MIN_DATA_MEMBERS` 추가
      검증: `python -c "import config; print(config.THEME_MAX_TOP1_SHARE)"`
- [x] 2. `rollup_themes` 에 `top1_share` / `top1_name` / `coverage` / `featured` 계산 추가
      검증: 단위 테스트 (한 종목이 90% 끄는 테마 → featured False)
- [x] 3. `analyze()` 의 `themes_top` / `themes_bottom` / 헤드라인을 featured 기준으로 전환
      검증: 단위 테스트 (featured 가 없으면 전체로 폴백)
- [x] 4. `templates/dashboard.html` 에 1등 종목 비중 표시 (칩 + 표 컬럼)
      검증: 재빌드 후 로컬 서버로 육안 확인
- [x] 5. `tests/test_analyze.py` 갱신
      검증: `python -m pytest -q` 전부 통과
- [x] 6. 실제 데이터로 재실행
      검증: 상위 테마에서 삼성 계열 5개 연속이 사라짐
- [x] 6b. (추가) 상단 노출을 개수로만 자르니 순매수 테마가
      '빠져나간 테마' 칸에 섞였다. 부호로 자르도록 고치고 회귀 테스트 추가
- [ ] 7. 커밋 (tzdata 수정 / 지표 추가 / 첫 리포트 분리)

## 2차 · 겹치는 테마 묶기 (완전 포함만)

- [x] 8. `rollup_themes` 가 수급 잡힌 종목코드를 함께 넘기게 한다
      검증: 단위 테스트
- [x] 9. `merge_contained_themes` 추가 — 구성종목이 다른 테마에 완전히
      포함되면 흡수. 대표는 featured 우선, 그다음 금액순
      검증: 단위 테스트 (featured 테마가 비featured 에 먹히지 않는지)
- [x] 10. `analyze()` 에서 묶기 → featured 필터 → top/bottom 순서로 적용
      검증: 단위 테스트
- [x] 11. 대시보드에 '+N개 테마' 칩과 펼쳤을 때 묶인 테마 목록 표시
      검증: 재빌드 후 육안
- [x] 12. 재실행 · 커밋

## 3차 · 확정치 전환 + 과거 데이터 백필

- [x] 13. `src/history.py` — 일별 확정 투자자매매동향으로 수급 시계열 수집
      (종목당 1콜에 30일치. 유니버스 = 테마∩코스피 752종목)
      검증: 단위 테스트 + 실제 1종목 교차검증
- [x] 14. `store.save_report` 에 latest.json 갱신 여부 플래그 추가
      검증: 백필이 latest.json 을 과거로 덮지 않는지
- [x] 15. `collect_all` 의 final 단계를 확정치로 전환 (flash 는 가집계 유지)
      검증: 단위 테스트
- [x] 16. `main.py --backfill N` 추가
      검증: 2주 백필 후 자금 이동이 표시되는지
- [x] 17. 커버리지가 올라가 임계값 재조정이 필요한지 확인
      검증: 실측 분포 재확인
- [x] 17b. (추가) 커버리지 분모에 조회조차 안 하는 코스닥 종목이 섞여
      멀쩡한 테마가 얇아 보였다. 분모를 유니버스로 교체
- [x] 17c. (추가) 휴장일 다음날 자금 이동이 빠지던 버그. 달력상 전 영업일 대신
      실제 존재하는 최근 리포트를 쓰도록 변경
- [x] 18. 재실행 · 커밋 · 푸시

## 4차 · 과거분 얇게 저장

- [x] 19. `store.slim` 추가. 날짜별 파일은 자금 흐름 시계열만, latest.json 은 전체
      검증: 얇은 보관본으로 자금이동·연속일수를 재계산해 결과 동일 확인
- [x] 20. 기존 13일치 변환 (192KB → 24KB)
      검증: 자금이동 짝 8개 동일, 연속일수 불일치 0개

## 5차 · 과거 날짜 보기

용량은 문제가 아님을 실측으로 확인했다 (전체 저장해도 연 9MB, git 델타압축 기준).
6개월 후 얇게 다시 쓰는 방식은 .git 을 줄이지 못한다 (예전 버전이 남으므로).
그래서 저장 전략이 아니라 '기능' 으로 접근한다.

- [x] 21. `config.FULL_REPORT_DAYS` 추가 (6개월 ≈ 125영업일)
- [x] 22. `save_report` 가 전체를 쓰고, 보관 기간이 지난 파일만 얇게 줄인다
      검증: 단위 테스트 (경계에서 정확히 잘리는지, 이미 얇은 건 건너뛰는지)
- [x] 23. `index.json` 에 전체본이 있는 날짜 목록 추가
      검증: 단위 테스트
- [x] 24. 대시보드에 날짜 선택 추가. 고르면 그날 리포트를 불러 다시 그린다
      검증: node 로 렌더 함수 실행
- [x] 25. 얇은 날짜는 테마 순위를 themes 에서 유도해 보여준다 (구성종목 표는 없음)
      검증: 얇은 보관본으로 렌더해 테마가 나오는지
- [x] 25b. (버그) themeTable 이 축약본에서 undefined.toFixed() 로 터짐. 빈칸 처리
- [x] 25c. (버그) featured 필드가 없는 축약본에서 쏠림 필터가 무력화됨.
      필드 유무를 구분하도록 수정하고 store 축약본에 featured 를 남김
- [x] 26. 재실행 · 커밋 · 푸시


---

## 6차 · 자금 이동 확대 + 업종 구성종목

- [x] 27. 자금 이동 경로 8→13, 유입/유출 5→10 (config 로 분리)
      검증: 금액이 맞는 경우/어긋나는 경우 둘 다 테스트
- [x] 28. 저장된 13일치 자금 이동 재계산 (KIS 호출 없이)
- [x] 29. masters.load_stock_sectors — 마스터에서 종목-업종 매핑
      검증: 오프셋을 전수 탐색으로 확정, 유니버스 99.5% 매핑
- [x] 30. analyze.attach_sector_stocks — 업종별 상위 종목 부착
      검증: 단위 테스트 4개
- [x] 31. 업종 칸을 눌러 구성종목 펼치기 (구성종목 있는 칸만 버튼)
      검증: node 로 실제 렌더 실행, 24개 버튼 확인

## 7차 · 예약 실행 실패 (2026-08-28)

- [x] 32. 실행 기록으로 원인 확인 — 예약이 19:53 UTC(KST 04:53)에 발사돼
      당일 수급이 없어 '리포트 생성' 에서 exit 1. 미장 관련 스케줄은 없음
- [x] 33. 수급이 있을 수 없는 시점이면 수집 전에 exit 0
- [x] 34. 마감 후~확정 전 구간의 빈 결과는 exit 0, 그 뒤는 exit 1
      검증: tests/test_schedule.py (두 판정이 서로 모순 없는지 포함)

## 8차 · 미국 시세 현지 날짜 (2026-08-28)

- [x] 35. KIS 해외주식 일봉이 미완료 세션(프리마켓)도 행으로 준다는 것 확인
      야후와 대조해 8/28 행이 시간외 값임을 확증
- [x] 36. last_closed_us_date / _closed_only 추가. _series 와 _fetch_yahoo 에 적용
      시간대는 ZoneInfo("America/New_York") — 서머타임 때문에 고정 오프셋 불가
- [x] 37. 검증: 엔비디아 -1.06% -> +8.74%, as_of 8/28 -> 8/27
      tests/test_overseas.py 9개

---

# 다음 작업

갱신 2026-09-02 저녁.

## 지금 상태

사이트 https://nimblebody.github.io/kospi-flow/ 정상.

- 리포트는 매일 자동 생성된다. GitHub 예약이 아니라 Cloudflare Worker 가
  정시에 workflow_dispatch 를 때린다 (worker/README.md 참고).
- 화면만 고친 푸시는 pages.yml 이 KIS 호출 없이 배포한다.
- 미국 시세는 현지 마감이 끝난 세션만 쓴다.
- 휴장일·장 전 실행은 실패가 아니라 정상 종료로 끝난다.

## 확인이 남은 것 (2026-09-02 기준)

Cloudflare 크론을 매일 실행(`* * *`)으로 방금 다시 등록했다. 아직 한 번도
정상 발사를 눈으로 못 봤다. **Cloudflare → Worker → Logs** 에서 본다.

주의. 크론이 정상 발사돼도 오늘 리포트가 이미 있으면 건너뛰고,
건너뛰면 GitHub Actions 에 흔적이 안 남는다. Actions 탭만 보고 판단하면 안 된다.

- [ ] 평일 16:30 에 `cron 30 7 * * *: {"skipped":false ...}` 가 찍히는가
- [ ] 18:00 / 20:00 에 `{"skipped":true, "오늘 ... 리포트가 이미 있습니다"}` 인가
- [ ] 토요일에 파이프라인이 `휴장일입니다` 로 40초 만에 끝나는가
- [ ] 리포트가 나온 날 텔레그램 알림이 오는가 (안 오면 어딘가 막힌 것)

## 손볼 것

### 1. 다크모드 상단바 색

templates/dashboard.html 의 theme-color 가 밝은 색 하나뿐이다. 폰이 다크모드면
화면은 어두운데 상태바 주변만 밝게 뜬다.

    <meta name="theme-color" content="#f7f7f5">
    <!-- 아래를 추가 -->
    <meta name="theme-color" content="#131416" media="(prefers-color-scheme: dark)">

manifest 의 theme_color(#f6f6f4)와 페이지 meta(#f7f7f5)가 미묘하게 다른 것도
같이 맞추면 좋다. src/render.py 의 MANIFEST 딕셔너리에 있다.

### 2. 아이콘 교체

src/render.py 의 _png() 가 의존성 없이 그린 막대 두 개짜리 임시 도형이다
(192px 가 570바이트). 직접 만든 PNG 를 web/icon-192.png / icon-512.png 로
넣으면 된다. _png() 는 파일이 없을 때만 그리므로 덮어쓰면 유지된다.

## 새 환경에서 처음 할 일

`.env` 는 gitignore 라 clone/pull 해도 따라오지 않는다.

    cp .env.example .env
    # KIS_APP_KEY(36자) / KIS_APP_SECRET(180자) 를 채운다

윈도우면 tzdata 가 필요하다 (`ZoneInfo("Asia/Seoul")` 이 터진다).

    pip install -r requirements.txt

키 확인.

    python -X utf8 -c "from src.kis import KisClient; print(len(KisClient().token))"

화면 확인 (file:// 로 열면 CORS 로 막힌다. 반드시 서버로).

    python -m http.server -d web 8000

테스트.

    python tests/test_analyze.py
    python tests/test_history.py
    python tests/test_store.py
    python tests/test_schedule.py
    python tests/test_overseas.py

## 알아둘 것

- 워크플로가 매일 web/data 를 커밋하므로, 로컬 작업 전에 git pull --rebase 를
  먼저 하자. 충돌하면 web/data 는 최신 코드로 생성한 쪽을 남기면 된다.
- 백필 재실행은 3분 15초 걸린다 (752종목, 8스레드).
  KIS 는 30일치만 주므로 그 이상 과거는 못 채운다.
- 확정 수급은 장 마감(15:30) 뒤 15~20분이면 올라온다. 실측으로 15:34 에는
  없었고 15:44 / 16:24 에는 있었다. 크론 16:30 은 여유를 둔 값이다.
- 설계 판단 근거는 전부 context-notes.md 에 있다.
