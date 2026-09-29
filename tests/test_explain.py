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
