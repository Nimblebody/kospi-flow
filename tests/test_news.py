# 뉴스 수집·요약(src/news.py) 검증 — 모델이 준 번호를 실제 기사에 맞추는 부분이 핵심
"""실행: python tests/test_news.py

모델에게 제목을 다시 쓰게 하지 않고 번호만 고르게 했다. 그 번호가 엉뚱하면
엉뚱한 기사에 링크가 걸린다. 그래서 범위 밖·중복 번호를 어떻게 처리하는지 본다.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import news as N  # noqa: E402


def _rows(n=12):
    return [
        {
            "title": f"기사{i}", "source": "연합뉴스", "time": f"09-01 1{i%10}:00",
            "url": f"https://example.com/{i}", "direct": i % 2 == 0,
        }
        for i in range(1, n + 1)
    ]


def _fake_ask(answer):
    def go(prompt, schema=None):
        return answer
    return go


def run_with(monkey_answer, rows=None):
    rows = rows or _rows()
    orig = N._ask
    N._ask = _fake_ask(monkey_answer)
    try:
        return N.summarize("2026-09-01", rows)
    finally:
        N._ask = orig


# ------------------------------------------------------------ 제목 정리
def test_clean_strips_outlet_and_entities():
    assert N._clean("코스피 상승 - 연합뉴스") == "코스피 상승"
    assert N._clean('일본 증시 하락…&quot;우려&quot;') == '일본 증시 하락…"우려"'
    assert N._clean("AT&amp;T 실적") == "AT&T 실적"


def test_stock_filter_drops_unrelated():
    assert N._is_stock("코스피 6,830 마감") is True
    assert N._is_stock("손흥민 결승골") is False


# ------------------------------------------------------------ 번호 매핑
def test_index_maps_to_the_real_article():
    out = run_with({
        "headline": "한 줄", "points": ["요약."],
        "top": [{"index": 3, "why": "이유."}, {"index": 1, "why": "이유2."}],
    })
    assert [t["title"] for t in out["top"]] == ["기사3", "기사1"]
    assert out["top"][0]["url"] == "https://example.com/3"
    assert out["top"][0]["why"] == "이유."


def test_out_of_range_index_is_dropped():
    """없는 번호를 주면 버린다. 엉뚱한 기사에 링크가 걸리면 안 된다."""
    out = run_with({
        "headline": "h", "points": [],
        "top": [{"index": 999, "why": "x"}, {"index": 0, "why": "y"},
                {"index": -1, "why": "z"}, {"index": 2, "why": "정상."}],
    })
    assert [t["title"] for t in out["top"]] == ["기사2"]


def test_duplicate_index_is_dropped():
    out = run_with({
        "headline": "h", "points": [],
        "top": [{"index": 5, "why": "a"}, {"index": 5, "why": "b"}],
    })
    assert len(out["top"]) == 1


def test_non_integer_index_is_dropped():
    out = run_with({
        "headline": "h", "points": [],
        "top": [{"index": "3", "why": "문자열"}, {"index": None, "why": "널"},
                {"index": 4, "why": "정상."}],
    })
    assert [t["title"] for t in out["top"]] == ["기사4"]


def test_top_is_capped():
    rows = _rows(40)
    out = run_with(
        {"headline": "h", "points": [],
         "top": [{"index": i, "why": "w"} for i in range(1, 31)]},
        rows,
    )
    assert len(out["top"]) == N.TOP_N


# ------------------------------------------------------------ 실패 처리
def test_too_few_articles_gives_up():
    assert N.summarize("2026-09-01", _rows(5)) is None


def test_model_failure_returns_none():
    orig = N._ask
    N._ask = lambda *a, **k: None
    try:
        assert N.summarize("2026-09-01", _rows()) is None
    finally:
        N._ask = orig


def test_model_exception_does_not_propagate():
    """뉴스가 깨져도 파이프라인 전체가 죽으면 안 된다."""
    orig = N._ask
    def boom(*a, **k):
        raise RuntimeError("API 터짐")
    N._ask = boom
    try:
        assert N.summarize("2026-09-01", _rows()) is None
    finally:
        N._ask = orig


def test_schema_closes_every_object():
    """구조화 출력은 모든 객체에 additionalProperties: False 를 요구한다.

    빠뜨렸더니 API 가 스키마를 거부해 첫 실행이 통째로 실패했다.
    """
    def walk(node, path="root"):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False, path
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(N.SCHEMA)


# ------------------------------------------------------------ 수집 창
def test_window_days_covers_overnight():
    """01:30 에 돌면 어제와 오늘 이틀이 걸린다. 새벽 기사를 놓치면 안 된다."""
    from datetime import datetime, timedelta
    import config

    since = datetime(2026, 9, 2, 0, 0, tzinfo=config.KST)
    now = datetime(2026, 9, 3, 1, 30, tzinfo=config.KST)
    assert N._window_days(since, now) == ["2026-09-02", "2026-09-03"]


def test_window_days_same_day():
    from datetime import datetime
    import config

    since = datetime(2026, 9, 2, 0, 0, tzinfo=config.KST)
    now = datetime(2026, 9, 2, 18, 0, tzinfo=config.KST)
    assert N._window_days(since, now) == ["2026-09-02"]


def test_window_days_is_capped():
    """옛 날짜를 손으로 넣어도 무한정 훑지 않는다."""
    from datetime import datetime
    import config

    since = datetime(2026, 1, 1, 0, 0, tzinfo=config.KST)
    now = datetime(2026, 9, 2, 18, 0, tzinfo=config.KST)
    assert len(N._window_days(since, now)) == 3


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
