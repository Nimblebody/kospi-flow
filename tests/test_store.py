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


def test_slim_keeps_featured_so_past_dates_rank_the_same():
    """축약본만 있는 날짜도 최신 화면과 같은 기준으로 테마를 줄 세워야 한다."""
    s = store.slim({**FULL, "themes": [
        {"name": "고른테마", "net_eok": 100.0, "featured": True},
        {"name": "쏠린테마", "net_eok": 900.0, "featured": False},
    ]})
    assert [t["featured"] for t in s["themes"]] == [True, False]


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


# ------------------------------------------------------------ 보관 기간
def _with_tmp_data(fn):
    """config.DATA_DIR 을 임시 폴더로 바꿔 실제 파일로 검증한다."""
    import json as _json
    import tempfile
    import config

    with tempfile.TemporaryDirectory() as tmp:
        orig_dir, orig_days = config.DATA_DIR, config.FULL_REPORT_DAYS
        config.DATA_DIR = Path(tmp)
        try:
            fn(Path(tmp), _json)
        finally:
            config.DATA_DIR, config.FULL_REPORT_DAYS = orig_dir, orig_days


def test_retention_keeps_recent_full_and_slims_the_rest():
    def body(tmp, _json):
        import config
        config.FULL_REPORT_DAYS = 2

        for day in ("2026-08-25", "2026-08-26", "2026-08-27"):
            store.save_report({**FULL, "date": day}, make_latest=False)

        # 최근 2일은 전체본, 그 앞은 축약본
        for day, expect_full in (("2026-08-27", True), ("2026-08-26", True),
                                 ("2026-08-25", False)):
            rep = _json.loads((tmp / f"{day}.json").read_text(encoding="utf-8"))
            assert store.is_full(rep) is expect_full, day

        idx = _json.loads((tmp / "index.json").read_text(encoding="utf-8"))
        assert idx["full"] == ["2026-08-27", "2026-08-26"]
        assert len(idx["dates"]) == 3

    _with_tmp_data(body)


def test_retention_does_not_rewrite_already_slim_files():
    """이미 얇은 파일은 건드리지 않는다. 괜히 커밋 diff 만 생긴다."""
    def body(tmp, _json):
        import config
        config.FULL_REPORT_DAYS = 1

        store.save_report({**FULL, "date": "2026-08-25"}, make_latest=False)
        store.save_report({**FULL, "date": "2026-08-27"}, make_latest=False)
        old_path = tmp / "2026-08-25.json"
        before = old_path.read_bytes()
        assert not store.is_full(_json.loads(before))       # 이미 축약됨

        store.save_report({**FULL, "date": "2026-08-27"}, make_latest=False)
        assert old_path.read_bytes() == before              # 그대로

    _with_tmp_data(body)


def test_latest_stays_full_even_when_date_file_is_slimmed():
    def body(tmp, _json):
        import config
        config.FULL_REPORT_DAYS = 0        # 전부 축약 대상

        store.save_report({**FULL, "date": "2026-08-27"}, make_latest=True)
        assert not store.is_full(_json.loads((tmp / "2026-08-27.json").read_text(encoding="utf-8")))
        assert store.is_full(_json.loads((tmp / "latest.json").read_text(encoding="utf-8")))

    _with_tmp_data(body)


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
