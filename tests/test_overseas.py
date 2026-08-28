# 미국 시세의 현지 날짜 처리(src/overseas.py) 검증
"""실행: python tests/test_overseas.py

한국은 KST, 미국은 현지 시각이라 하루가 어긋난다. KIS 해외주식 일봉은
장이 열리기도 전에 그날 날짜로 행을 주기 때문에, 그걸 그대로 종가로 쓰면
'간밤 마감' 이 아니라 '지금 프리마켓' 이 된다.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from src import overseas as O  # noqa: E402


def kst(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=config.KST)


# ------------------------------------------------------------ 최신 마감일
def test_korean_afternoon_points_at_previous_us_session():
    """실제로 틀렸던 상황. KST 8/28 오후엔 현지가 8/28 새벽이라 장 전이다."""
    assert O.last_closed_us_date(kst(2026, 8, 28, 17, 25)) == "20260827"


def test_korean_dawn_after_us_close_uses_that_session():
    """KST 8/28 06:30 = ET 8/27 17:30. 방금 끝난 8/27 이 최신이다."""
    assert O.last_closed_us_date(kst(2026, 8, 28, 6, 30)) == "20260827"


def test_during_us_session_still_uses_previous_day():
    """KST 8/29 00:30 = ET 8/28 11:30. 장중이라 8/28 은 아직 안 끝났다."""
    assert O.last_closed_us_date(kst(2026, 8, 29, 0, 30)) == "20260827"


def test_right_at_us_close_counts_as_done():
    # ET 8/27 16:00 = KST 8/28 05:00
    assert O.last_closed_us_date(kst(2026, 8, 28, 5, 0)) == "20260827"
    assert O.last_closed_us_date(kst(2026, 8, 28, 4, 59)) == "20260826"


def test_daylight_saving_is_handled():
    """서머타임. 고정 오프셋으로 두면 반년마다 한 시간 틀어진다.

    겨울(EST, UTC-5)의 마감 16:00 은 KST 06:00 이다.
    """
    assert O.last_closed_us_date(kst(2026, 1, 15, 6, 30)) == "20260114"
    assert O.last_closed_us_date(kst(2026, 1, 15, 5, 30)) == "20260113"


# ------------------------------------------------------------ 필터
NVDA = [("20260828", 225.56), ("20260827", 227.98), ("20260826", 209.66)]


def test_incomplete_session_row_is_dropped():
    """KIS 가 준 프리마켓 행(8/28)을 버리고 8/27 마감을 최신으로 삼는다."""
    out = O._closed_only(NVDA, kst(2026, 8, 28, 17, 25))
    assert out[0] == ("20260827", 227.98)
    assert all(d <= "20260827" for d, _ in out)


def test_completed_session_row_is_kept():
    """장이 끝난 뒤에는 그날 행을 그대로 쓴다."""
    out = O._closed_only(NVDA, kst(2026, 8, 29, 6, 0))
    assert out[0] == ("20260828", 225.56)


def test_move_uses_the_right_two_closes():
    """등락률이 프리마켓이 아니라 실제 마감 기준으로 계산돼야 한다.

    잘못 계산하면 225.56 vs 227.98 = -1.06%.
    바르게 계산하면 227.98 vs 209.66 = +8.74%.
    """
    m = O._move(O._closed_only(NVDA, kst(2026, 8, 28, 17, 25)))
    assert m["date"] == "2026-08-27"
    assert m["value"] == 227.98
    assert m["chg_pct"] == 8.74


def test_series_applies_the_filter():
    """_series 를 거치는 모든 경로(지수·주식)가 자동으로 걸러져야 한다."""
    raw = [
        {"xymd": "20991231", "clos": "999"},      # 미래 = 미완료
        {"xymd": "20260827", "clos": "227.98"},
        {"xymd": "20260826", "clos": "209.66"},
    ]
    out = O._series(raw)
    assert out[0][0] == "20260827"


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok   {name}")
            except AssertionError as exc:
                failed += 1
                print(f"  FAIL {name}: {exc}")
    print("\n실패" if failed else "\n전부 통과")
    sys.exit(1 if failed else 0)
