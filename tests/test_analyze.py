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


def test_top1_share_measures_single_stock_dominance():
    # 삼성전자 800억 / SK하이닉스 300억 -> 절대값 합 1100 중 800
    rows = A.rollup_themes(SNAP, {"반도체": ["005930", "000660"]}, min_members=2)
    semi = rows[0]
    assert semi["top1_name"] == "삼성전자"
    assert semi["top1_share"] == round(800 / 1100, 3)
    assert semi["coverage"] == 1.0


def test_top1_share_uses_absolute_sum_not_net():
    # 매수 500 / 매도 -500 이라 순매수 합은 0. 분모가 net 이면 0으로 나눈다.
    snap = {**SNAP, "flows": [
        _flow("A", "매수주", 250, 250),
        _flow("B", "매도주", -250, -250),
    ]}
    rows = A.rollup_themes(snap, {"혼조": ["A", "B"]}, min_members=2)
    assert rows[0]["net_eok"] == 0.0
    assert rows[0]["top1_share"] == 0.5      # 폭발하지 않는다


def test_featured_excludes_single_stock_driven_theme():
    # 한 종목이 압도 -> 종목 수가 충분해도 탈락
    snap = {**SNAP, "flows": [
        _flow("A", "대장주", 900, 0),
        _flow("B", "곁다리1", 10, 0),
        _flow("C", "곁다리2", 10, 0),
    ]}
    rows = A.rollup_themes(snap, {"쏠림테마": ["A", "B", "C"]}, min_members=2)
    assert rows[0]["top1_share"] > 0.5
    assert rows[0]["featured"] is False


def test_featured_excludes_thin_sample():
    # 고르게 나뉘었지만 데이터가 2종목뿐 -> 탈락
    snap = {**SNAP, "flows": [_flow("A", "가", 100, 0), _flow("B", "나", 100, 0)]}
    rows = A.rollup_themes(snap, {"얇은테마": ["A", "B", "C", "D"]}, min_members=2)
    assert rows[0]["top1_share"] == 0.5
    assert rows[0]["members_with_data"] == 2
    assert rows[0]["featured"] is False


def test_featured_keeps_broad_theme():
    snap = {**SNAP, "flows": [
        _flow("A", "가", 100, 0), _flow("B", "나", 90, 0), _flow("C", "다", 80, 0),
    ]}
    rows = A.rollup_themes(snap, {"고른테마": ["A", "B", "C"]}, min_members=2)
    assert rows[0]["featured"] is True


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


def test_analyze_top_themes_exclude_dominated_ones():
    """쏠린 테마는 themes 에는 남지만 themes_top 에서는 빠진다."""
    snap = {**SNAP, "flows": [
        _flow("A", "대장주", 900, 0), _flow("B", "곁다리1", 10, 0), _flow("C", "곁다리2", 10, 0),
        _flow("D", "가", 100, 0), _flow("E", "나", 90, 0), _flow("F", "다", 80, 0),
    ]}
    themes = {"쏠림테마": ["A", "B", "C"], "고른테마": ["D", "E", "F"]}
    rep = A.analyze(snap, themes, {}, theme_source="테스트")

    # 전체 목록에는 둘 다 있고, 금액이 큰 쏠림테마가 여전히 1위
    assert [t["name"] for t in rep["themes"]] == ["쏠림테마", "고른테마"]
    # 상단 노출은 고른테마 하나뿐
    assert [t["name"] for t in rep["themes_top"]] == ["고른테마"]
    assert rep["themes_demoted"] == 1
    # 헤드라인도 고른테마를 가리킨다
    assert any("고른테마" in h for h in rep["headline"])
    assert not any("쏠림테마" in h for h in rep["headline"])


def test_coverage_denominator_uses_queried_universe():
    """조회조차 안 한 종목은 커버리지 분모에서 빠져야 한다.

    테마 마스터에는 코스닥 종목도 섞여 있는데 유니버스가 코스피뿐이다.
    분모에 넣으면 멀쩡한 테마가 '표본이 얇다' 고 잘못 표시된다.
    """
    snap = {**SNAP, "flows": [
        _flow("A", "가", 100, 0), _flow("B", "나", 90, 0), _flow("C", "다", 80, 0),
    ], "universe": ["A", "B", "C", "D"]}
    # 마스터에는 6종목이지만 조회 대상은 4종목뿐
    themes = {"테마": ["A", "B", "C", "D", "코스닥1", "코스닥2"]}

    rows = A.rollup_themes(snap, themes)
    assert rows[0]["members_total"] == 4          # 6 이 아니라 4
    assert rows[0]["members_with_data"] == 3
    assert rows[0]["coverage"] == 0.75            # 3/4, 3/6 이 아니다


def test_coverage_falls_back_when_no_universe():
    """가집계(flash)에는 유니버스가 없다. 예전대로 전체를 센다."""
    snap = {**SNAP, "flows": [
        _flow("A", "가", 100, 0), _flow("B", "나", 90, 0), _flow("C", "다", 80, 0),
    ]}
    rows = A.rollup_themes(snap, {"테마": ["A", "B", "C", "D"]})
    assert rows[0]["members_total"] == 4
    assert rows[0]["coverage"] == 0.75


def test_merge_absorbs_fully_contained_theme():
    """작은 테마의 종목이 큰 테마에 전부 들어있으면 흡수된다."""
    snap = {**SNAP, "flows": [
        _flow("A", "가", 100, 0), _flow("B", "나", 90, 0), _flow("C", "다", 80, 0),
    ]}
    themes = {"큰테마": ["A", "B", "C"], "부분집합": ["A", "B"]}
    rows = A.rollup_themes(snap, themes)
    hosts = A.merge_contained_themes(rows)

    assert [h["name"] for h in hosts] == ["큰테마"]
    big = next(r for r in rows if r["name"] == "큰테마")
    assert big["merged"] == ["부분집합"]
    assert "_codes" not in big          # 내부 필드는 JSON 에 나가면 안 된다


def test_merge_keeps_partially_overlapping_themes_apart():
    """일부만 겹치는 테마는 묶지 않는다."""
    snap = {**SNAP, "flows": [
        _flow("A", "가", 100, 0), _flow("B", "나", 90, 0),
        _flow("C", "다", 80, 0), _flow("D", "라", 70, 0),
    ]}
    themes = {"하나": ["A", "B", "C"], "둘": ["B", "C", "D"]}
    hosts = A.merge_contained_themes(A.rollup_themes(snap, themes))
    assert len(hosts) == 2


def test_merge_prefers_featured_theme_as_host():
    """대표성 없는 테마가 호스트가 되어 멀쩡한 테마를 데려가면 안 된다.

    금액만으로 순서를 잡으면 쏠린큰테마가 호스트가 되고, 그 아래 깔린
    고른작은테마까지 featured 필터에서 통째로 사라진다.
    """
    snap = {**SNAP, "flows": [
        _flow("A", "대장주", 5000, 0),
        _flow("B", "나", 10, 0), _flow("C", "다", 10, 0), _flow("D", "라", 10, 0),
    ]}
    themes = {"쏠린큰테마": ["A", "B", "C", "D"], "고른작은테마": ["B", "C", "D"]}
    rows = A.rollup_themes(snap, themes)
    big = next(r for r in rows if r["name"] == "쏠린큰테마")
    small = next(r for r in rows if r["name"] == "고른작은테마")
    assert big["featured"] is False               # 대장주가 99% 를 끈다
    assert small["featured"] is True              # 셋이 고르게 나눠 가진다
    assert abs(big["net_eok"]) > abs(small["net_eok"])

    hosts = A.merge_contained_themes(rows)
    # featured 인 고른작은테마가 먼저 호스트가 되고, 쏠린큰테마는 그 부분집합이
    # 아니므로(오히려 상위집합) 흡수되지 않는다. 둘 다 살아남는다.
    assert set(h["name"] for h in hosts) == {"쏠린큰테마", "고른작은테마"}
    assert small["merged"] == []


def test_analyze_reports_merge_and_demote_counts():
    snap = {**SNAP, "flows": [
        _flow("A", "가", 100, 0), _flow("B", "나", 90, 0), _flow("C", "다", 80, 0),
    ]}
    themes = {"큰테마": ["A", "B", "C"], "부분집합": ["A", "B"]}
    rep = A.analyze(snap, themes, {}, theme_source="테스트")
    assert rep["themes_merged"] == 1
    assert [t["name"] for t in rep["themes_top"]] == ["큰테마"]
    assert rep["themes_top"][0]["merged"] == ["부분집합"]


def test_theme_buckets_split_by_sign_not_by_count():
    """순매수 테마가 '빠져나간 테마' 칸에 섞이면 안 된다."""
    snap = {**SNAP, "flows": [
        _flow("A", "가", 100, 0), _flow("B", "나", 90, 0), _flow("C", "다", 80, 0),
        _flow("D", "라", -100, 0), _flow("E", "마", -90, 0), _flow("F", "바", -80, 0),
        _flow("G", "사", 5, 0), _flow("H", "아", 4, 0), _flow("I", "자", 3, 0),
    ]}
    themes = {"양수큰": ["A", "B", "C"], "음수": ["D", "E", "F"], "양수작은": ["G", "H", "I"]}
    rep = A.analyze(snap, themes, {}, theme_source="테스트")

    assert all(t["net_eok"] > 0 for t in rep["themes_top"])
    assert all(t["net_eok"] < 0 for t in rep["themes_bottom"])
    assert [t["name"] for t in rep["themes_bottom"]] == ["음수"]


def test_analyze_falls_back_when_nothing_qualifies():
    """전부 걸러지는 날에는 화면이 비지 않도록 전체 목록으로 되돌린다."""
    snap = {**SNAP, "flows": [_flow("A", "대장주", 900, 0), _flow("B", "곁다리", 10, 0)]}
    rep = A.analyze(snap, {"쏠림테마": ["A", "B"]}, {}, theme_source="테스트")
    assert rep["themes_top"] and rep["themes_top"][0]["name"] == "쏠림테마"
    assert rep["themes_demoted"] == 0


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
