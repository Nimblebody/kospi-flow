# 증시 해설(src/explain.py)의 20:30 확정 갱신 판정 검증 — 수급이 크게 바뀔 때만 다시 쓰는지
"""실행: python tests/test_explain.py

20:30 에 해설 4개를 전부 다시 쓰면 해설 비용이 두 배다. 국내 수급이 크게 달라졌을 때만
국내 해설·업종 요약을 다시 쓰고, 미국 쪽은 늘 16:30 것을 쓴다. 모델은 부르지 않는다.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from src import explain as E  # noqa: E402


def _rep(frgn=-27428.7, orgn=-9292.8, top=("전기차", 2211.9), bottom=("아이폰", -42234.9), **extra):
    themes = [{"name": top[0], "net_eok": top[1]}, {"name": "ESS", "net_eok": 1736.9},
              {"name": bottom[0], "net_eok": bottom[1]}]
    return {"date": "2026-09-28", "stage": "final",
            "investors": {"foreign_eok": frgn, "institution_eok": orgn},
            "themes": themes, "themes_top": themes[:2], "themes_bottom": themes[2:], **extra}


OLD_EXPLAIN = {
    "model": "m",
    "markets": {"kr": {"verdict": "16:30 국내", "flow_z": -1.0, "sources": []},
                "us": {"verdict": "16:30 미국", "sources": []}},
    "sectors": {"kr": {"note": "16:30 업종"}, "us": {"note": "16:30 섹터"}},
}


def _run(prev, cur):
    """모델 대신 호출 기록만 남기는 가짜로 explain 을 돌린다."""
    calls = []
    orig = E._one, E.sectors, E.flow_z, config.ANTHROPIC_API_KEY
    E._one = lambda report, history, market: calls.append(("해설", market)) or {"verdict": f"새 {market}"}
    E.sectors = lambda report, news, market: calls.append(("섹터", market)) or {"note": f"새 {market}"}
    E.flow_z = lambda report, history: -2.5
    config.ANTHROPIC_API_KEY = "test"
    try:
        return E.explain(cur, [], prev=prev), calls
    finally:
        E._one, E.sectors, E.flow_z, config.ANTHROPIC_API_KEY = orig


# ------------------------------------------------------------ 판정
def test_small_change_is_not_big():
    s = E.flow_shift(_rep(), _rep(frgn=-27500.0, orgn=-9250.0, top=("전기차", 2296.9)))
    assert s["big"] == []
    assert "외국인 -27,429억 → -27,500억" in s["note"]
    assert "가장 많이 바뀐 테마는 전기차(+85억)" in s["note"]
    assert "그대로 둔다" in s["note"]


def test_ten_percent_and_300_eok_is_big():
    """9/10 기관처럼 18.9% 정정이면 다시 쓴다."""
    s = E.flow_shift(_rep(orgn=-9292.8), _rep(orgn=-9292.8 * 1.189))
    assert s["big"] and s["big"][0].startswith("기관")


def test_large_percent_but_tiny_amount_is_not_big():
    """기관이 +50억 → +90억이면 80% 지만 40억뿐이다."""
    assert E.flow_shift(_rep(orgn=50.0), _rep(orgn=90.0))["big"] == []


def test_sign_flip_is_big():
    assert E.flow_shift(_rep(orgn=200.0), _rep(orgn=-400.0))["big"]


def test_top_theme_change_is_big():
    s = E.flow_shift(_rep(), _rep(top=("반도체", 2500.0)))
    assert any("자금 유입 1위 테마 전기차 → 반도체" in b for b in s["big"])


# ------------------------------------------------------------ 다시 쓰기
def test_small_change_reuses_everything_and_calls_nothing():
    prev = _rep(explain=OLD_EXPLAIN)
    out, calls = _run(prev, _rep(frgn=-27500.0))
    assert calls == []
    assert out["markets"]["kr"]["verdict"] == "16:30 국내"
    assert out["markets"]["kr"]["flow_z"] == -2.5       # σ 는 지금 수급으로 다시 잰다
    assert out["sectors"] == OLD_EXPLAIN["sectors"]
    assert out["refresh"]["redone"] is False and "그대로 둔다" in out["refresh"]["note"]


def test_big_change_redoes_korea_only():
    """미국장은 20:30 에 아직 안 열렸다. 미국 해설·섹터는 16:30 것을 쓴다."""
    prev = _rep(explain=OLD_EXPLAIN)
    out, calls = _run(prev, _rep(orgn=-12000.0))
    assert sorted(calls) == [("섹터", "kr"), ("해설", "kr")]
    assert out["markets"]["kr"]["verdict"] == "새 kr"
    assert out["markets"]["us"]["verdict"] == "16:30 미국"
    assert out["sectors"]["us"]["note"] == "16:30 섹터"
    assert out["refresh"]["redone"] is True and out["refresh"]["why"]


def test_first_run_of_the_day_makes_everything():
    out, calls = _run(None, _rep())
    assert len(calls) == 4 and "refresh" not in out


def test_flash_or_other_day_is_not_reused():
    """잠정치(flash) 리포트나 다른 날 리포트의 해설은 다시 쓰지 않는다."""
    for prev in (_rep(stage="flash", explain=OLD_EXPLAIN) | {"stage": "flash"},
                 _rep(explain=OLD_EXPLAIN) | {"date": "2026-09-25"}):
        out, calls = _run(prev, _rep())
        assert len(calls) == 4 and "refresh" not in out


def test_missing_part_in_the_morning_is_filled_in():
    """16:30 에 미국 해설이 실패해 없었다면 20:30 에 채운다."""
    old = {"markets": {"kr": OLD_EXPLAIN["markets"]["kr"]}, "sectors": {"kr": {"note": "업종"}}}
    out, calls = _run(_rep(explain=old), _rep())
    assert sorted(calls) == [("섹터", "us"), ("해설", "us")]



# ------------------------------------------------------------ 업종 요약
def test_leaked_article_numbers_are_dropped():
    """effort low 에서 '관련 기사 있음[17,38,39]' 가 본문에 새어 나왔다(9/28 비교)."""
    assert E._drop_refs("SK하이닉스 -5.0% 대규모 매도, 관련 기사 있음[17,38,39].") ==         "SK하이닉스 -5.0% 대규모 매도."
    assert E._drop_refs("하이브 +3.4% 상승, 관련 기사 있음[33].") == "하이브 +3.4% 상승."
    assert E._drop_refs("대우건설 +7.0% 기사[5] 주도.") == "대우건설 +7.0% 주도."
    assert E._drop_refs("화학 +3.2%, 반도체 [장비] 강세.") == "화학 +3.2%, 반도체 [장비] 강세."


def test_sectors_ask_with_low_effort_and_clean_lines():
    asked = []
    secs = [{"name": f"업종{i}", "chg_pct": 3.0 - i, "stocks": [{"name": f"종목{i}", "chg_pct": 1.0}]}
            for i in range(5)]
    report = {"date": "2026-09-28", "sectors": secs, "indices": [], "investors": {}}

    def fake_ask(prompt, schema=None, **kw):
        asked.append(kw)
        return {"note": "큰 그림[3].", "rows": [{"name": s["name"], "line": "끌었다, 관련 기사 있음[1,2]."}
                                             for s in secs]}

    orig = E._ask, E.headlines
    E._ask, E.headlines = fake_ask, lambda queries, on_date, limit=None: []
    try:
        out = E.sectors(report, [], "kr")
    finally:
        E._ask, E.headlines = orig
    assert asked == [{"effort": "low"}]
    assert out["note"] == "큰 그림."
    assert all(r["line"] == "끌었다." for r in out["rows"])



# ------------------------------------------------------------ 오타(흔치 않은 글자)
def test_odd_syllables_catches_real_typo():
    """2026-09-29 조선 저녁 분석에 실제로 나온 오타."""
    assert E.odd_syllables("임단협 교섭이 순조롭게 타결 쪭으로 흐르면") == {"쪭"}
    assert E.odd_syllables("임단협 교섭이 순조롭게 타결 쪽으로 흐르면") == set()


def test_rare_syllable_in_the_prompt_is_allowed():
    """기사 제목·종목 이름에 있는 드문 글자(똠얌 등)는 오타가 아니다."""
    assert E.odd_syllables("똠얌 가게 매출", prompt="[기사] 똠얌 프랜차이즈") == set()
    assert E.odd_syllables("똠얌 가게 매출") == {"똠"}


class _Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Res:
    class usage:
        input_tokens = 10
        output_tokens = 5

    def __init__(self, text):
        self.content = [_Block(text)]


def _ask_with(answers):
    """anthropic 을 가짜로 바꿔 _ask 를 돌린다. 호출 횟수와 결과를 돌려준다."""
    import types
    calls = []

    class _Messages:
        def create(self, **kw):
            calls.append(kw)
            return _Res(answers[min(len(calls), len(answers)) - 1])

    fake = types.SimpleNamespace(Anthropic=lambda: types.SimpleNamespace(messages=_Messages()))
    orig = sys.modules.get("anthropic")
    sys.modules["anthropic"] = fake
    try:
        return E._ask("프롬프트", {"type": "object"}), len(calls)
    finally:
        if orig is None:
            sys.modules.pop("anthropic", None)
        else:
            sys.modules["anthropic"] = orig


def test_typo_answer_is_asked_again_once():
    got, n = _ask_with(['{"t": "타결 쪭으로"}', '{"t": "타결 쪽으로"}'])
    assert n == 2 and got == {"t": "타결 쪽으로"}


def test_clean_answer_is_not_asked_again():
    got, n = _ask_with(['{"t": "타결 쪽으로"}'])
    assert n == 1 and got == {"t": "타결 쪽으로"}


def test_typo_twice_gives_up_and_keeps_the_answer():
    """두 번째도 이상하면 더 부르지 않는다. 분석을 통째로 버리는 것보다 낫다."""
    got, n = _ask_with(['{"t": "쪭"}', '{"t": "쪭"}'])
    assert n == 2 and got == {"t": "쪭"}


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
