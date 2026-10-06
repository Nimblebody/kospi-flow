# KIS 클라이언트(src/kis.py)의 접근토큰 발급 검증 — 1분 1회 제한(EGW00133)에 걸리면 기다렸다 다시 받는지
"""실행: python tests/test_kis.py

20:30 에 확정 갱신과 조선 저녁이 연달아 도는데, 휴장일엔 앞 작업이 금방 끝나 뒤 작업이
1분 안에 토큰을 다시 받으려다 403 EGW00133 으로 죽었다(2026-10-04·05). KIS 는 부르지 않는다.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import kis as K  # noqa: E402

LIMIT = '{"error_code":"EGW00133","error_description":"접근토큰 발급 잠시 후 다시 시도하세요(1분당 1회)"}'
OK = '{"access_token":"tok","expires_in":86400}'


class _Res:
    def __init__(self, status, text):
        self.status_code, self.text = status, text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        import json
        return json.loads(self.text)


def _client(answers):
    """토큰 캐시·잠자기·세션을 가짜로 바꾼 클라이언트. (클라이언트, 부른 횟수, 잔 시간들)"""
    c = K.KisClient(app_key="k", app_secret="s", base_url="https://example.invalid")
    calls, slept = [], []

    def post(url, **kw):
        calls.append(url)
        return _Res(*answers[min(len(calls), len(answers)) - 1])

    c._session.post = post
    c._load_cached_token = lambda: None
    c._save_token = lambda token, expires_in: None
    K.time.sleep = slept.append
    return c, calls, slept


def _run(answers):
    orig = K.time.sleep
    try:
        c, calls, slept = _client(answers)
        try:
            return c.token, calls, slept
        except RuntimeError as exc:
            return exc, calls, slept
    finally:
        K.time.sleep = orig


def test_rate_limited_token_waits_a_minute_and_retries():
    tok, calls, slept = _run([(403, LIMIT), (200, OK)])
    assert tok == "tok" and len(calls) == 2 and slept == [61]


def test_token_first_try_does_not_wait():
    tok, calls, slept = _run([(200, OK)])
    assert tok == "tok" and len(calls) == 1 and slept == []


def test_gives_up_after_three_tries():
    out, calls, slept = _run([(403, LIMIT)] * 5)
    assert isinstance(out, RuntimeError) and len(calls) == 3 and slept == [61, 61]


def test_other_errors_are_not_retried():
    """키가 틀린 것 같은 다른 403 은 기다려도 소용없다. 바로 실패한다."""
    out, calls, slept = _run([(403, '{"error_code":"EGW00103","error_description":"유효하지 않은 AppKey"}')])
    assert isinstance(out, RuntimeError) and len(calls) == 1 and slept == []


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
