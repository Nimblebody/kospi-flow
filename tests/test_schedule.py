# 예약 실행이 엉뚱한 시각에 깨어났을 때의 처리(main.data_not_ready_yet) 검증
"""실행: python tests/test_schedule.py

GitHub 크론은 제때 발사된다는 보장이 없다. 실제로 09:30 UTC 예약이
19:53 UTC(KST 04:53)에 돈 적이 있고, 그 시각엔 당일 수급이 없어 실패했다.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
import main  # noqa: E402


def at(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=config.KST)


def test_dawn_run_is_not_a_failure():
    """실제로 터졌던 상황. KST 04:53 에 당일 수급을 찾으면 없는 게 맞다."""
    assert main.data_not_ready_yet("20260828", at(2026, 8, 28, 4, 53)) is True


def test_before_market_close_is_not_ready():
    assert main.data_not_ready_yet("20260828", at(2026, 8, 28, 9, 0)) is True
    assert main.data_not_ready_yet("20260828", at(2026, 8, 28, 15, 29)) is True


def test_after_data_time_is_a_real_failure():
    """장이 끝나고도 비었으면 그건 알려야 한다. 조용히 넘기면 안 된다."""
    hh, mm = config.DATA_READY_KST
    assert main.data_not_ready_yet("20260828", at(2026, 8, 28, hh, mm)) is False
    assert main.data_not_ready_yet("20260828", at(2026, 8, 28, 18, 30)) is False


def test_boundary_is_exact():
    hh, mm = config.DATA_READY_KST
    assert main.data_not_ready_yet("20260828", at(2026, 8, 28, hh, mm - 1)) is True
    assert main.data_not_ready_yet("20260828", at(2026, 8, 28, hh, mm)) is False


def test_past_date_empty_is_always_a_failure():
    """지난 거래일을 --date 로 지정했는데 비었으면 시각과 무관하게 문제다."""
    assert main.data_not_ready_yet("20260827", at(2026, 8, 28, 4, 53)) is False
    assert main.data_not_ready_yet("20260827", at(2026, 8, 28, 23, 59)) is False


def test_future_date_is_never_ready():
    assert main.data_not_ready_yet("20260901", at(2026, 8, 28, 23, 59)) is True


def test_too_early_skips_collection():
    """장중에는 752종목을 긁어볼 것도 없다."""
    ch, cm = config.MARKET_CLOSE_KST
    assert main.too_early_to_collect("20260828", at(2026, 8, 28, 4, 53)) is True
    assert main.too_early_to_collect("20260828", at(2026, 8, 28, 13, 0)) is True
    assert main.too_early_to_collect("20260828", at(2026, 8, 28, ch, cm - 1)) is True
    assert main.too_early_to_collect("20260828", at(2026, 8, 28, ch, cm)) is False
    assert main.too_early_to_collect("20260828", at(2026, 8, 28, 18, 30)) is False


def test_future_date_is_always_too_early():
    """아직 오지 않은 날은 장 마감 시각과 무관하게 수집할 게 없다."""
    assert main.too_early_to_collect("20260901", at(2026, 8, 28, 23, 59)) is True


def test_past_date_is_never_mid_session():
    """--date 로 지난 거래일을 주면 시각과 무관하게 수집한다."""
    assert main.too_early_to_collect("20260827", at(2026, 8, 28, 4, 53)) is False


def test_two_gates_are_consistent():
    """장중 조기종료와 '아직 안 나옴' 판정이 서로 모순되지 않아야 한다.

    장중(마감 전)이면 데이터도 당연히 '아직'이어야 한다. 반대로 뒤집히면
    수집은 건너뛰는데 빈 결과는 실패로 처리하는 모순이 생긴다.
    """
    for hh in range(0, 24):
        for mm in (0, 29, 30, 31, 59):
            now = at(2026, 8, 28, hh, mm)
            if main.too_early_to_collect("20260828", now):
                assert main.data_not_ready_yet("20260828", now), f"{hh}:{mm}"


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
