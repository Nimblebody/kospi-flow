# 리포트 저장(src/store.py) 단위 테스트 — 얇은 보관본이 계산에 충분한지 검증
"""실행: python tests/test_store.py"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import analyze as A  # noqa: E402
from src import store  # noqa: E402

FULL = {
    "date": "2026-08-27",
    "stage": "final",
    "collected_at": "2026-08-27T18:00:00+09:00",
    "theme_source": "테스트",
    "headline": ["한 줄"],
    "investors": {"foreign_eok": 1.0, "institution_eok": 2.0},
    "indices": [{"name": "코스피", "value": 2700.0}],
    "rotation": {"available": True, "pairs": [], "inflow": [], "outflow": []},
    "themes": [
        {
            "name": "반도체", "net_eok": 100.0, "foreign_eok": 60.0,
            "institution_eok": 40.0, "streak": 2, "top1_share": 0.4,
            "coverage": 1.0, "featured": True, "merged": [], "breadth": 1.0,
            "members_total": 3, "members_with_data": 3, "avg_chg_pct": 1.2,
            "stocks": [{"code": "005930", "name": "삼성전자", "net_eok": 60.0}],
        }
    ],
    "themes_top": [{"name": "반도체", "stocks": [{"code": "005930"}]}],
    "themes_bottom": [],
    "top_buy": [{"code": "005930"}],
    "top_sell": [],
    "spikes": [],
    "sectors": [],
}


def test_slim_drops_the_bulky_parts():
    s = store.slim(FULL)
    for gone in ("themes_top", "themes_bottom", "top_buy", "top_sell", "spikes", "sectors"):
        assert gone not in s, gone
    assert "stocks" not in s["themes"][0]


def test_slim_keeps_what_rotation_and_streaks_need():
    """얇게 만든 뒤에도 자금 이동과 연속일수가 계산돼야 한다."""
    s = store.slim(FULL)
    today = [{"name": "반도체", "net_eok": 500.0}, {"name": "2차전지", "net_eok": -100.0}]

    rot = A.compute_rotation(today, s["themes"])
    assert rot["available"] is True
    assert rot["inflow"][0]["name"] == "반도체"
    assert rot["inflow"][0]["prev_eok"] == 100.0

    streaks = A.compute_streaks(today, [s["themes"]])
    assert streaks["반도체"] == 2      # 오늘 + 보관본 하루


def test_slim_keeps_the_daily_money_series():
    """돈의 흐름 시계열 — 테마별 외국인·기관 분해와 시장 전체 투자자 합계."""
    s = store.slim(FULL)
    t = s["themes"][0]
    assert (t["foreign_eok"], t["institution_eok"]) == (60.0, 40.0)
    assert s["investors"]["foreign_eok"] == 1.0
    assert s["headline"] == ["한 줄"]
    assert s["rotation"]["available"] is True


def test_slim_is_much_smaller():
    full = len(json.dumps(FULL, ensure_ascii=False).encode())
    thin = len(json.dumps(store.slim(FULL), ensure_ascii=False).encode())
    assert thin < full * 0.6, f"{thin} vs {full}"


def test_slim_survives_missing_keys():
    """가집계(flash)나 과거 백필처럼 일부 키가 없는 리포트도 깨지지 않는다."""
    s = store.slim({"date": "2026-08-27", "themes": [{"name": "반도체", "net_eok": 1.0}]})
    assert s["date"] == "2026-08-27"
    assert s["themes"] == [{"name": "반도체", "net_eok": 1.0}]


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
