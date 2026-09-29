# 조선 탭(src/ship.py) 검증 — 기사 거름, 종목 묶기, 근거 기사 매핑, 묶음 합산
"""실행: python tests/test_ship.py"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from src import ship as S  # noqa: E402
from src import store  # noqa: E402


# ------------------------------------------------------------ 기사 거름
def test_keep_catches_shipbuilding_news():
    assert S.keep("한화오션, LNG선 2척 수주")
    assert S.keep("조선주 일제히 강세…마스가 기대")
    assert S.keep("HMM, 컨테이너 운임 반등에 상승")


def test_keep_ignores_newspaper_names_with_joseon():
    """'조선' 한 글자로 거르면 조선일보·조선비즈·북한 조선중앙통신이 걸린다."""
    assert not S.keep("[조선비즈] 코스피 2% 하락 마감")
    assert not S.keep("조선일보 사설: 금리 인하 서둘러야")
    assert not S.keep("조선중앙통신 '미사일 시험 성공' 주장")


def test_keep_ignores_unrelated():
    assert not S.keep("삼성전자 HBM4 양산 돌입")


# ------------------------------------------------------------ 종목 묶기
def test_universe_dedupes_and_drops_delisted():
    """앞 묶음에 들어간 종목은 뒤에서 다시 안 넣고, 마스터에 없는 코드는 뺀다."""
    orig = (S.masters.load_themes, S.masters.load_stock_names, S.masters.load_kosdaq_names)
    S.masters.load_themes = lambda: ({
        "조선": ["042660", "010620"],             # 010620 = 상장폐지(마스터에 없음)
        "조선기자재": ["017960", "042660"],        # 042660 은 이미 조선에 있다
        "해운": ["011200"],
        "LNG": ["036460", "017960"],
    }, "테스트")
    S.masters.load_stock_names = lambda: {"042660": "한화오션", "011200": "HMM",
                                           "036460": "한국가스공사"}
    S.masters.load_kosdaq_names = lambda: {"017960": "한국카본"}
    try:
        uni = S.universe()
    finally:
        S.masters.load_themes, S.masters.load_stock_names, S.masters.load_kosdaq_names = orig

    assert [(u["name"], u["group"]) for u in uni] == [
        ("한화오션", "조선"), ("한국카본", "기자재"), ("HMM", "해운"), ("한국가스공사", "LNG"),
    ]


def test_universe_drops_excluded_misfits():
    """테마 마스터가 잘못 넣은 종목(HLB = 제약)은 뺀다."""
    orig = (S.masters.load_themes, S.masters.load_stock_names, S.masters.load_kosdaq_names)
    S.masters.load_themes = lambda: ({"조선기자재": ["017960", "028300"]}, "테스트")
    S.masters.load_stock_names = lambda: {}
    S.masters.load_kosdaq_names = lambda: {"017960": "한국카본", "028300": "HLB"}
    try:
        names = [u["name"] for u in S.universe()]
    finally:
        S.masters.load_themes, S.masters.load_stock_names, S.masters.load_kosdaq_names = orig
    assert names == ["한국카본"]


# ------------------------------------------------------------ 종목 표
def _uni():
    return [
        {"code": "A", "name": "가", "group": "조선"},
        {"code": "B", "name": "나", "group": "조선"},
        {"code": "C", "name": "다", "group": "해운"},
        {"code": "D", "name": "라", "group": "LNG"},   # 시세 없음 -> 빠져야 한다
    ]


def _q():
    return {
        "A": {"price": 10000, "chg_pct": 3.0, "amount_eok": 500.0, "volume": 1, "mcap_eok": 1},
        "B": {"price": 20000, "chg_pct": -1.0, "amount_eok": 300.0, "volume": 1, "mcap_eok": 1},
        "C": {"price": 5000, "chg_pct": 0.0, "amount_eok": 100.0, "volume": 1, "mcap_eok": 1},
    }


def test_merge_drops_stocks_without_quote():
    rows = S.merge(_uni(), _q(), {})
    assert [r["code"] for r in rows] == ["A", "B", "C"]


def _fl(close, chg, amount, **extra):
    return {"close": close, "close_chg_pct": chg, "close_amount_eok": amount,
            "close_volume": 1, "frgn_eok": 0.0, "orgn_eok": 0.0,
            "frgn_5d_eok": 0.0, "orgn_5d_eok": 0.0, **extra}


def test_evening_uses_official_close_not_live_price():
    """20:30 의 현재가는 애프터마켓 마지막 가격일 수 있다. 종가·등락은 공식 종가로."""
    q = _q()   # A 현재가 10000, 거래대금 500억
    rows = S.merge(_uni(), q, {"A": _fl(9500, 2.5, 480.0)})
    a = next(r for r in rows if r["code"] == "A")
    assert (a["price"], a["chg_pct"], a["amount_eok"]) == (9500, 2.5, 480.0)


def test_after_hours_is_live_price_vs_official_close():
    rows = S.merge(_uni(), _q(), {"A": _fl(9500, 2.5, 480.0)})
    a = next(r for r in rows if r["code"] == "A")
    assert a["ah_price"] == 10000
    assert a["ah_chg_pct"] == round((10000 / 9500 - 1) * 100, 2)
    assert a["ah_amount_eok"] == 20.0          # 현재가 쪽 500억 - 정규장 480억


def test_no_after_hours_when_live_equals_close():
    """현재가가 공식 종가와 같으면 애프터마켓 움직임을 만들지 않는다."""
    rows = S.merge(_uni(), _q(), {"A": _fl(10000, 3.0, 500.0)})
    a = next(r for r in rows if r["code"] == "A")
    assert "ah_price" not in a and "ah_chg_pct" not in a


def test_morning_keeps_live_quote_when_no_official_close():
    """아침엔 수급(=공식 종가)을 안 받는다. 현재가 API 값을 그대로 쓴다."""
    rows = S.merge(_uni(), _q(), {})
    a = next(r for r in rows if r["code"] == "A")
    assert (a["price"], a["chg_pct"]) == (10000, 3.0)
    assert "ah_price" not in a


def test_internal_close_fields_do_not_leak():
    rows = S.merge(_uni(), _q(), {"A": _fl(9500, 2.5, 480.0)})
    a = next(r for r in rows if r["code"] == "A")
    assert not any(k.startswith("close") for k in a)


def test_group_summary():
    fl = {"A": {"frgn_eok": 10.0, "orgn_eok": -3.0}, "B": {"frgn_eok": -4.0, "orgn_eok": 1.0}}
    g = {x["name"]: x for x in S.group_summary(S.merge(_uni(), _q(), fl))}
    assert g["조선"]["count"] == 2
    assert g["조선"]["avg_chg_pct"] == 1.0
    assert (g["조선"]["up"], g["조선"]["down"]) == (1, 1)
    assert g["조선"]["amount_eok"] == 800.0
    assert (g["조선"]["frgn_eok"], g["조선"]["orgn_eok"]) == (6.0, -2.0)
    assert "LNG" not in g                      # 시세 있는 종목이 없는 묶음은 안 나온다
    assert "frgn_eok" not in g["해운"]          # 수급을 못 받은 묶음은 합계를 안 만든다


def test_evening_prompt_asks_for_flows_only_when_present():
    """수급 숫자가 없는데 '수급을 이유와 연결하라' 고 시키면 모델이 '없다' 로 한 칸을 쓴다."""
    with_fl = S.merge(_uni(), _q(), {"A": {"frgn_eok": 1.0, "orgn_eok": 1.0,
                                           "frgn_5d_eok": 1.0, "orgn_5d_eok": 1.0}})
    no_fl = S.merge(_uni(), _q(), {})
    p1 = S._evening_prompt("2026-09-29", with_fl, S.group_summary(with_fl), [])
    p2 = S._evening_prompt("2026-09-29", no_fl, S.group_summary(no_fl), [])
    # 공통 규칙에도 '없으면 수급 이야기는 하지 않는다' 가 있어서, 할 일 문장으로 가른다
    assert "5일 누적)을 이유와 연결한다" in p1 and "오늘은 수급 숫자가 없다" not in p1
    assert "오늘은 수급 숫자가 없다" in p2 and "5일 누적)을 이유와 연결한다" not in p2


def test_listing_shows_how_many_outlets_ran_the_story():
    arts = [{"time": "09-28 16:36", "source": "A", "title": "수주", "dup": 15},
            {"time": "09-28 17:00", "source": "B", "title": "자사주", "dup": 1},
            {"time": "09-28 17:10", "source": "C", "title": "옛 형식"}]
    lines = S._listing(arts).splitlines()
    assert lines[0].endswith("(같은 소식 15건)")
    assert "같은 소식" not in lines[1] and "같은 소식" not in lines[2]


def test_evening_prompt_says_after_close_news_is_not_todays_reason():
    """장 마감 뒤 공시를 그날 주가의 이유로 엮는 실수(9/28, 세 방식 모두)를 규칙으로 막는다."""
    rows = S.merge(_uni(), _q(), {})
    p = S._evening_prompt("2026-09-28", rows, S.group_summary(rows), [])
    assert "15:30 이후에 처음 나온" in p and "다음 거래일" in p


# ------------------------------------------------------------ 근거 기사
POOL = [{"title": f"기사{i}", "url": f"https://x/{i}"} for i in range(1, 11)]


def test_map_sources_keeps_valid_indices_in_order():
    out = S.map_sources(POOL, [{"index": 3, "why": "a"}, {"index": 1, "why": "b"}])
    assert [o["title"] for o in out] == ["기사3", "기사1"]
    assert out[0]["why"] == "a"


def test_map_sources_drops_bad_indices():
    out = S.map_sources(POOL, [
        {"index": 0, "why": ""}, {"index": 99, "why": ""}, {"index": "2", "why": ""},
        {"index": True, "why": ""}, {"index": 4, "why": "ok"}, {"index": 4, "why": "dup"},
    ])
    assert [o["title"] for o in out] == ["기사4"]


def test_map_sources_is_capped():
    out = S.map_sources(POOL, [{"index": i, "why": ""} for i in range(1, 11)])
    assert len(out) == S.SOURCES_N


# ------------------------------------------------------------ 기사 창
def test_morning_window_starts_at_last_evening():
    """월요일 아침에 주말 기사가 빠지지 않게 마지막 저녁 분석부터 본다."""
    now = datetime(2026, 9, 28, 8, 0, tzinfo=config.KST)          # 월
    fri = datetime(2026, 9, 25, 20, 31, tzinfo=config.KST)
    assert S._since("morning", now, {"generated_at": fri.isoformat()}) == fri


def test_morning_window_falls_back_when_evening_is_stale_or_missing():
    now = datetime(2026, 9, 28, 8, 0, tzinfo=config.KST)
    old = now - timedelta(days=10)
    want = datetime(2026, 9, 27, 20, 0, tzinfo=config.KST)
    assert S._since("morning", now, {"generated_at": old.isoformat()}) == want
    assert S._since("morning", now, None) == want


def test_evening_window_is_today():
    now = datetime(2026, 9, 28, 20, 30, tzinfo=config.KST)
    assert S._since("evening", now) == datetime(2026, 9, 28, 0, 0, tzinfo=config.KST)


def test_quotes_from_flows_uses_official_close_and_makes_no_after_hours():
    """지난 날짜는 현재가(=지금 값) 대신 그날 공식 종가를 시세로 쓴다.

    그대로 두면 오늘 가격이 그날 종가로 적히고, 오늘 가격과 그날 종가의 차이가
    가짜 애프터마켓 등락으로 잡힌다.
    """
    fl = {"A": _fl(9500, 2.5, 480.0), "B": _fl(0, 0.0, 0.0)}   # B 는 그날 행 없음
    q = S.quotes_from_flows(fl)
    assert list(q) == ["A"]
    assert (q["A"]["price"], q["A"]["chg_pct"], q["A"]["amount_eok"]) == (9500, 2.5, 480.0)
    rows = S.merge(_uni(), q, fl)
    assert [r["code"] for r in rows] == ["A"]
    assert "ah_price" not in rows[0]


def test_morning_window_prefers_as_of_over_generated_at():
    """지난 날짜를 다시 만든 저녁 분석은 generated_at 이 늦다. 기사 창은 as_of 로 잡는다."""
    now = datetime(2026, 9, 30, 8, 0, tzinfo=config.KST)
    as_of = datetime(2026, 9, 28, 20, 30, tzinfo=config.KST)
    ev = {"as_of": as_of.isoformat(), "generated_at": "2026-09-29T14:00:00+09:00"}
    assert S._since("morning", now, ev) == as_of


def test_past_date_only_for_evening():
    try:
        S.build("morning", date="20200101")
    except ValueError:
        return
    raise AssertionError("아침은 지난 날짜로 다시 만들 수 없어야 한다")


# ------------------------------------------------------------ 스키마
def test_schemas_close_every_object():
    """구조화 출력은 모든 객체에 additionalProperties: False 를 요구한다."""
    def walk(node, path):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False, path
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(S.EVENING_SCHEMA, "evening")
    walk(S.MORNING_SCHEMA, "morning")


def test_evening_call_is_constrained():
    call = S.EVENING_SCHEMA["properties"]["call"]["properties"]
    assert call["direction"]["enum"] == ["상승", "하락", "보합"]
    assert call["confidence"]["enum"] == ["높음", "보통", "낮음"]


# ------------------------------------------------------------ 저장
def test_save_ship_keeps_both_slots():
    with tempfile.TemporaryDirectory() as tmp:
        orig = config.DATA_DIR
        config.DATA_DIR = Path(tmp)
        try:
            store.save_ship({"slot": "evening", "date": "2026-09-28", "headline": "저녁"})
            store.save_ship({"slot": "morning", "date": "2026-09-29", "headline": "아침"})
            cur = json.loads((Path(tmp) / "ship.json").read_text(encoding="utf-8"))
        finally:
            config.DATA_DIR = orig
    assert cur["evening"]["headline"] == "저녁"
    assert cur["morning"]["headline"] == "아침"


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
