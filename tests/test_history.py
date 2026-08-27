# 과거 수급 시계열 수집(src/history.py) 단위 테스트
"""실행: python tests/test_history.py"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import history as H  # noqa: E402


class FakeKis:
    """investor_trade_by_stock_daily 만 흉내 내는 가짜 클라이언트."""

    token = "fake-token"   # collect_series 가 스레드 시작 전에 한 번 읽는다

    def __init__(self, rows_by_code, fail=()):
        self.rows_by_code = rows_by_code
        self.fail = set(fail)
        self.calls = []

    def investor_trade_by_stock_daily(self, code, date):
        self.calls.append((code, date))
        if code in self.fail:
            raise RuntimeError("조회 실패")
        return self.rows_by_code.get(code, [])


def _row(date, frgn, orgn, prsn=0, clpr=10000, chg=1.0):
    return {
        "stck_bsop_date": date, "stck_clpr": str(clpr), "prdy_ctrt": str(chg),
        "acml_vol": "1000",
        "frgn_ntby_tr_pbmn": str(frgn),
        "orgn_ntby_tr_pbmn": str(orgn),
        "prsn_ntby_tr_pbmn": str(prsn),
    }


def test_business_days_skips_weekend():
    # 2026-08-30 은 일요일
    assert H.business_days("20260830", 3) == ["20260826", "20260827", "20260828"]
    assert H.business_days("20260827", 5) == [
        "20260821", "20260824", "20260825", "20260826", "20260827",
    ]


def test_one_call_per_stock_fills_many_dates():
    """종목당 1콜인데 응답의 여러 날짜가 한 번에 채워진다."""
    kis = FakeKis({"005930": [_row("20260827", 100, 50), _row("20260826", -30, 20)]})
    out = H.collect_series(kis, ["005930"], "20260827", {"20260827", "20260826"})

    assert len(kis.calls) == 1
    assert out["20260827"]["005930"]["frgn"] == 100 * H.PBMN_UNIT
    assert out["20260827"]["005930"]["net"] == 150 * H.PBMN_UNIT
    assert out["20260826"]["005930"]["net"] == -10 * H.PBMN_UNIT


def test_dates_outside_range_are_ignored():
    kis = FakeKis({"005930": [_row("20260827", 100, 50), _row("20250101", 999, 999)]})
    out = H.collect_series(kis, ["005930"], "20260827", {"20260827"})
    assert list(out) == ["20260827"]


def test_zero_flow_rows_are_dropped():
    """거래가 없던 날은 담지 않는다. 안 그러면 테마 구성종목 수만 부풀려진다."""
    kis = FakeKis({"005930": [_row("20260827", 0, 0, 0)]})
    out = H.collect_series(kis, ["005930"], "20260827", {"20260827"})
    assert out["20260827"] == {}


def test_failed_stock_does_not_stop_the_sweep():
    kis = FakeKis(
        {"005930": [_row("20260827", 100, 50)], "000660": [_row("20260827", 7, 3)]},
        fail=["005930"],
    )
    out = H.collect_series(kis, ["005930", "000660"], "20260827", {"20260827"})
    assert "005930" not in out["20260827"]
    assert out["20260827"]["000660"]["net"] == 10 * H.PBMN_UNIT


def test_snapshot_shape_is_analyzable():
    snap = H.make_snapshot("20260827", {"005930": {"code": "005930", "net": 1.0}})
    for key in ("date", "stage", "collected_at", "flows", "market", "volume_leaders"):
        assert key in snap, key
    assert snap["stage"] == "final"
    assert snap["market"] == {"indices": [], "sectors": []}


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
