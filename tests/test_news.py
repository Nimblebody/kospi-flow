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


ASKED: list[dict] = []   # 가짜 _ask 가 받은 옵션(effort 등)


def _fake_ask(answer):
    def go(prompt, schema=None, **kw):
        ASKED.append(kw)
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



# ------------------------------------------------------------ 같은 소식 묶기
def _gather_with(titles):
    """제목 목록을 피드에서 온 기사처럼 넣고 gather 결과를 돌려준다. 앞 제목일수록 이르다."""
    from datetime import datetime
    import config

    base = datetime(2026, 9, 28, 16, 0, tzinfo=config.KST)
    rows = [{"title": t, "source": "매체", "time": "", "at": base.replace(minute=i),
             "url": f"https://example.com/{i}", "direct": True} for i, t in enumerate(titles)]
    orig = N._from_feeds, N._from_google
    N._from_feeds = lambda since, keep=None: [dict(r) for r in rows]
    N._from_google = lambda days, since, queries=None, keep=None: []
    try:
        out, _ = N.gather(base.replace(hour=0), until=base.replace(hour=20))
    finally:
        N._from_feeds, N._from_google = orig
    return out


def test_rewritten_titles_of_one_story_become_one():
    """같은 공시를 매체마다 제목을 바꿔 쓴다(9/28 실제 제목). 하나로 묶고 몇 건인지 센다."""
    out = _gather_with([
        "[속보] 한화오션, 6800억원 규모 LNG운반선 2척 수주…지난해 매출",
        "한화오션, LNG운반선 2척 6800억원 수주...9월에만 2.9조원",
        "한화오션, 아프리카 선주로부터 6800억원 규모 LNGC 2척 수주",
        "한화오션, 336억원 규모 자사주 취득 나서…임직원 주식보상",
    ])
    assert len(out) == 2, [r["title"] for r in out]
    lead = next(r for r in out if "LNG" in r["title"])
    assert lead["dup"] == 3
    assert lead["title"].startswith("[속보]")      # 가장 이른 기사가 대표
    assert next(r for r in out if "자사주" in r["title"])["dup"] == 1


def test_different_stories_with_shared_words_stay_apart():
    """같은 날 상장한 다른 종목, 같은 회사의 다른 소식은 따로 둔다."""
    out = _gather_with([
        "[특징주] 빅웨이브로보틱스, 코스닥 상장 첫날 +182%대 급등",
        "[특징주] 글로벌테크놀로지, 코스닥 상장 첫날 +144%대 급등",
        "HD현대중공업, 안전 투자 위해 170억 추가 집행",
        "HD현대중공업, 29일 임단협 교섭 재개",
    ])
    assert len(out) == 4, [r["title"] for r in out]


def test_no_chaining_through_look_alikes():
    """A~B, B~C 가 닮았어도 A 와 C 가 안 닮았으면 C 는 따로. 대표하고만 견준다."""
    a = "코스피, 美국채금리 부담에 약보합세…등락 거듭"
    b = "코스피, 美국채금리 부담에 약세…중국 증시 하락 출발"
    c = "[올댓차이나] 중국 증시 하락 출발…상하이지수 0.2%↓"
    ga, gb, gc = N._grams(a), N._grams(b), N._grams(c)
    assert N._alike(ga, gb) and N._alike(gb, gc) and not N._alike(ga, gc)
    out = _gather_with([a, b, c])
    assert len(out) == 2



# ------------------------------------------------------------ 네이버 검색
class _Resp:
    def __init__(self, items):
        self._items = items

    def raise_for_status(self):
        pass

    def json(self):
        return {"items": self._items}


def _naver_item(title, when, desc="요약", url="https://www.example.co.kr/a/1"):
    return {"title": title, "description": desc, "pubDate": when,
            "link": "https://n.news.naver.com/x", "originallink": url}


def _with_naver(pages, keys=True):
    """requests.get 을 가짜로 바꿔 네이버 응답을 차례로 돌려준다. 부른 횟수도 센다."""
    import config

    calls = []

    def fake_get(url, headers=None, timeout=None, params=None):
        calls.append(params)
        return _Resp(pages[len(calls) - 1] if len(calls) <= len(pages) else [])

    orig = N.requests.get, config.NAVER_CLIENT_ID, config.NAVER_CLIENT_SECRET
    N.requests.get = fake_get
    config.NAVER_CLIENT_ID, config.NAVER_CLIENT_SECRET = ("id", "secret") if keys else ("", "")
    return calls, orig


def _restore(orig):
    import config
    N.requests.get, config.NAVER_CLIENT_ID, config.NAVER_CLIENT_SECRET = orig


def test_naver_rows_are_cleaned_and_windowed():
    from datetime import datetime
    import config

    since = datetime(2026, 9, 28, 0, 0, tzinfo=config.KST)
    until = datetime(2026, 9, 28, 20, 30, tzinfo=config.KST)
    page = [
        _naver_item("<b>한화오션</b>, LNG선 2척 수주 &quot;6800억&quot;", "Mon, 28 Sep 2026 21:00:00 +0900"),
        _naver_item("<b>한화오션</b>, LNG선 2척 수주", "Mon, 28 Sep 2026 16:36:00 +0900",
                    desc="한화오션은 <b>LNG</b> 운반선 2척을", url="https://m.biz.chosun.com/a/2"),
        _naver_item("야구 소식", "Mon, 28 Sep 2026 15:00:00 +0900"),
        _naver_item("<b>한화오션</b> 지난주", "Sun, 27 Sep 2026 23:00:00 +0900"),
    ]
    calls, orig = _with_naver([page])
    try:
        rows = N._from_naver(since, until, ["한화오션"], keep=lambda t: "한화오션" in t)
    finally:
        _restore(orig)
    assert [r["title"] for r in rows] == ["한화오션, LNG선 2척 수주"]   # 창 밖·거름 탈락
    r = rows[0]
    assert r["desc"] == "한화오션은 LNG 운반선 2척을"
    assert r["source"] == "biz.chosun.com" and r["url"] == "https://m.biz.chosun.com/a/2"
    assert r["direct"] is True
    assert len(calls) == 1          # 옛 기사가 나오면 다음 쪽을 안 부른다


def test_naver_pages_until_it_reaches_since():
    from datetime import datetime
    import config

    since = datetime(2026, 9, 28, 0, 0, tzinfo=config.KST)
    until = datetime(2026, 9, 28, 20, 30, tzinfo=config.KST)
    p1 = [_naver_item("조선 A", "Mon, 28 Sep 2026 18:00:00 +0900")]
    p2 = [_naver_item("조선 B", "Mon, 28 Sep 2026 09:00:00 +0900"),
          _naver_item("조선 C", "Sun, 27 Sep 2026 22:00:00 +0900")]
    calls, orig = _with_naver([p1, p2])
    try:
        rows = N._from_naver(since, until, ["조선"], keep=lambda t: True)
    finally:
        _restore(orig)
    assert [r["title"] for r in rows] == ["조선 A", "조선 B"]
    assert [c["start"] for c in calls] == [1, 101]


def test_naver_without_keys_falls_back_to_feeds_and_google():
    """시크릿이 빠져도 조선 탭이 죽지 않는다. 예전 방식으로 모은다."""
    from datetime import datetime
    import config

    since = datetime(2026, 9, 28, 0, 0, tzinfo=config.KST)
    calls, orig = _with_naver([], keys=False)
    feeds = N._from_feeds, N._from_google
    N._from_feeds = lambda since, keep=None: [{
        "title": "피드 기사", "source": "매체", "time": "", "at": since.replace(hour=9),
        "url": "https://example.com/f", "direct": True}]
    N._from_google = lambda days, since, queries=None, keep=None: []
    try:
        out, _ = N.gather(since, until=since.replace(hour=20), naver=True)
    finally:
        N._from_feeds, N._from_google = feeds
        _restore(orig)
    assert calls == [] and [r["title"] for r in out] == ["피드 기사"]



def test_summary_asks_with_low_effort():
    """뉴스 요약은 effort low 로 부른다(9/28 비교에서 품질이 같고 32% 쌌다)."""
    ASKED.clear()
    run_with({"headline": "한 줄", "points": ["요약."], "top": [{"index": 1, "why": "이유."}]})
    assert ASKED == [{"effort": "low"}]


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
