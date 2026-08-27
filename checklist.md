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
