"""분석 로직 단위 테스트.  실행: python -m pytest -q  (또는 python tests/test_analyze.py)"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import analyze as A  # noqa: E402

EOK = 1e8


def _flow(code, name, frgn_eok, orgn_eok, chg=0.0):
    frgn, orgn = frgn_eok * EOK, orgn_eok * EOK
    return {
        "code": code, "name": name, "price": 10000, "chg_pct": chg,
        "volume": 1000, "frgn": frgn, "orgn": orgn, "net": frgn + orgn,
    }


SNAP = {
    "date": "2026-08-27",
    "stage": "flash",
    "collected_at": "2026-08-27T16:10:00+09:00",
    "flows": [
        _flow("005930", "삼성전자", 500, 300, 2.1),
        _flow("000660", "SK하이닉스", 200, 100, 1.4),
        _flow("373220", "LG에너지솔루션", -300, -200, -3.0),
        _flow("006400", "삼성SDI", -100, -50, -2.2),
    ],
    "market": {
        "indices": [{
            "name": "코스피", "value": 2700.0, "change": 20.0, "chg_pct": 0.75,
            "volume": 1, "amount": 1, "up": 500, "down": 300, "flat": 80,
        }],
        "sectors": [{"name": "전기전자", "chg_pct": 1.2, "amount": 1e12, "amount_share": 20.0}],
    },
    "volume_leaders": [{
        "code": "005930", "name": "삼성전자", "chg_pct": 2.1,
        "amount": 5000 * EOK, "volume": 1, "vol_increase_pct": 350.0, "tags": [],
    }],
}

THEMES = {"반도체": ["005930", "000660"], "2차전지": ["373220", "006400"]}
STOCK_THEMES = {
    "005930": ["반도체"], "000660": ["반도체"],
    "373220": ["2차전지"], "006400": ["2차전지"],
}


def test_theme_rollup_sums_and_sorts():
    rows = A.rollup_themes(SNAP, THEMES)
    assert [r["name"] for r in rows] == ["반도체", "2차전지"]
    semi = rows[0]
    assert semi["net_eok"] == 1100.0        # 500+300+200+100
    assert semi["foreign_eok"] == 700.0
    assert semi["institution_eok"] == 400.0
    assert semi["breadth"] == 1.0           # 두 종목 모두 순매수
    assert rows[1]["net_eok"] == -650.0
    assert rows[1]["breadth"] == 0.0


def test_theme_needs_minimum_members():
    rows = A.rollup_themes(SNAP, {"단일종목테마": ["005930"]}, min_members=2)
    assert rows == []


def test_investor_summary_estimates_retail_without_data():
    inv = A.summarize_investors(SNAP)
    assert inv["foreign_eok"] == 300.0      # 500+200-300-100
    assert inv["institution_eok"] == 150.0
    assert inv["retail_estimated"] is True
    assert inv["retail_proxy_eok"] == -450.0


def test_investor_summary_uses_real_retail_when_present():
    snap = {**SNAP, "flows": [{**f, "prsn": -10 * EOK} for f in SNAP["flows"]]}
    inv = A.summarize_investors(snap)
    assert inv["retail_estimated"] is False
    assert inv["retail_eok"] == -40.0


def test_rotation_pairs_outflow_to_inflow():
    today = [
        {"name": "반도체", "net_eok": 1000.0},
        {"name": "2차전지", "net_eok": -800.0},
        {"name": "방산", "net_eok": 100.0},
    ]
    prev = [
        {"name": "반도체", "net_eok": 0.0},
        {"name": "2차전지", "net_eok": 200.0},
        {"name": "방산", "net_eok": 50.0},
    ]
    rot = A.compute_rotation(today, prev)
    assert rot["available"] is True
    assert rot["inflow"][0]["name"] == "반도체"
    assert rot["inflow"][0]["delta_eok"] == 1000.0
    assert rot["outflow"][0]["name"] == "2차전지"
    assert rot["outflow"][0]["delta_eok"] == -1000.0
    assert rot["pairs"][0] == {"from": "2차전지", "to": "반도체", "amount_eok": 1000.0}


def test_rotation_needs_previous_report():
    assert A.compute_rotation([{"name": "반도체", "net_eok": 1.0}], None)["available"] is False


def test_streak_counts_consecutive_same_sign_days():
    today = [{"name": "반도체", "net_eok": 100.0}, {"name": "2차전지", "net_eok": -50.0}]
    history = [
        [{"name": "반도체", "net_eok": 80.0}, {"name": "2차전지", "net_eok": -20.0}],
        [{"name": "반도체", "net_eok": 30.0}, {"name": "2차전지", "net_eok": 40.0}],
        [{"name": "반도체", "net_eok": -5.0}],
    ]
    s = A.compute_streaks(today, history)
    assert s["반도체"] == 3     # 오늘 + 이틀
    assert s["2차전지"] == -2   # 오늘 + 하루


def test_spikes_filtered_by_threshold():
    spikes = A.find_spikes(SNAP, STOCK_THEMES)
    assert len(spikes) == 1
    assert spikes[0]["name"] == "삼성전자"
    assert spikes[0]["themes"] == ["반도체"]

    quiet = {**SNAP, "volume_leaders": [
        {**SNAP["volume_leaders"][0], "vol_increase_pct": 20.0}
    ]}
    assert A.find_spikes(quiet, STOCK_THEMES) == []


def test_analyze_end_to_end_shape():
    rep = A.analyze(SNAP, THEMES, STOCK_THEMES, theme_source="테스트")
    for key in ("headline", "themes", "themes_top", "rotation", "top_buy", "top_sell", "sectors"):
        assert key in rep, key
    assert rep["top_buy"][0]["name"] == "삼성전자"
    assert rep["top_sell"][0]["name"] == "LG에너지솔루션"
    assert rep["themes"][0]["streak"] == 1
    assert any("코스피" in h for h in rep["headline"])


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
