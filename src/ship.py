# 조선·기자재·해운·LNG 테마를 하루 두 번(08:00 / 20:30 KST) 모아 분석한다
"""
조선 테마 탭.

  아침 08:00  밤사이 뉴스 + 전날 저녁 분석 이어받기 + 오늘 볼 포인트
  저녁 20:30  종목별 종가·애프터마켓·외국인/기관 수급 + 오르내린 이유 + 전망

아침에 수급을 새로 받지 않는 이유. 확정 수급 API(FHPTJ04160001)는
00:00~15:40 에 호출 자체를 막는다(OPSQ2001 TIME LIMIT). 그래서 아침은 전날 저녁
분석을 이어받는다.

종목은 KIS 테마 마스터의 조선·조선기자재·해운·LNG 네 테마를 합친다. 테마 마스터에는
상장폐지된 코드도 남아 있어서(010620 HD현대미포가 현재가 0 으로 나왔다) 코스피·코스닥
마스터 어디에도 없는 코드는 뺀다.

기사 링크를 지어내지 않도록 모델은 기사 번호만 고른다. 번호와 실제 기사는 파이썬이
맞춘다(news.py 와 같은 방식).
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import config
from src import masters, news
from src.explain import _ask
from src.kis import KisClient

log = logging.getLogger(__name__)

# (화면 묶음 이름, 테마 마스터 이름). 앞 묶음에 먼저 들어간 종목은 뒤에서 다시 안 넣는다.
GROUPS = [
    ("조선", "조선"),
    ("기자재", "조선기자재"),
    ("해운", "해운"),
    ("LNG", "LNG"),
]

QUERIES = [
    "조선주", "조선업", "조선 수주", "조선기자재", "LNG선 발주",
    "HD현대중공업", "한화오션", "삼성중공업", "HD한국조선해양",
    "해운 운임", "HMM 팬오션", "마스가 조선",
]

# '조선' 한 글자로 거르면 조선일보·조선비즈·조선중앙통신이 걸린다.
# 업종을 가리키는 말과 회사명으로만 거른다.
HINTS = (
    "조선주", "조선업", "조선사", "조선소", "조선기자재", "조선3사", "조선 3사",
    "K-조선", "K조선", "조선株", "선박", "LNG선", "컨테이너선", "탱커", "유조선",
    "벌크선", "VLCC", "선가", "해운", "운임", "마스가", "MASGA", "함정", "MRO",
    "한화오션", "삼성중공업", "HD현대중공업", "HD한국조선", "HD현대마린",
    "HD현대미포", "HJ중공업", "한화엔진", "HMM", "팬오션", "대한해운",
    "한국카본", "동성화인텍", "세진중공업", "성광벤드", "태광",
    "LNG",
)

POOL = 50       # 모델에 넣을 기사 수
SOURCES_N = 8   # 근거로 남길 기사 수

DIRECTIONS = ["상승", "하락", "보합"]
CONFIDENCE = ["높음", "보통", "낮음"]


def keep(title: str) -> bool:
    return any(h in title for h in HINTS)


# ---------------------------------------------------------------- 종목
def universe() -> list[dict]:
    """네 테마 종목을 묶음 순서대로. 상장폐지(마스터에 없음)는 뺀다."""
    themes, _ = masters.load_themes()
    names = {**masters.load_kosdaq_names(), **masters.load_stock_names()}
    seen: set[str] = set()
    out: list[dict] = []
    for group, theme in GROUPS:
        for code in themes.get(theme, []):
            if code in seen or code not in names:
                continue
            seen.add(code)
            out.append({"code": code, "name": names[code], "group": group})
    return out


def _num(v) -> float:
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _parallel(fn, codes: list[str]) -> dict[str, dict]:
    """종목당 1콜을 동시에 띄운다. 초당 건수는 KisClient 의 레이트 리미터가 잡는다."""
    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=config.KIS_RATE_LIMIT_PER_SEC) as pool:
        for code, row in pool.map(fn, codes):
            if row:
                out[code] = row
    return out


def quotes(kis: KisClient, codes: list[str]) -> dict[str, dict]:
    """현재가 API. 장 전이면 전일 종가, 장 뒤면 당일 종가다."""
    def one(code: str):
        try:
            o = kis.get(
                "/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100",
                {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
            ).get("output") or {}
        except Exception as exc:
            log.debug("현재가 실패 %s: %s", code, exc)
            return code, None
        price = _num(o.get("stck_prpr"))
        if price <= 0:
            return code, None   # 거래정지·상장폐지
        return code, {
            "price": price,
            "chg_pct": _num(o.get("prdy_ctrt")),
            "amount_eok": round(_num(o.get("acml_tr_pbmn")) / 1e8, 1),   # 원 -> 억
            "volume": _num(o.get("acml_vol")),
            "mcap_eok": _num(o.get("hts_avls")),                         # 억
        }

    kis.token
    return _parallel(one, codes)


def after_hours(kis: KisClient, codes: list[str]) -> dict[str, dict]:
    """시간외 현재가 API(FHPST02300000).

    필드 이름은 폐지된 시간외 단일가용(ovtm_untp_*)이다. 2026-09-14 부터 생긴
    애프터마켓(16:00~20:00 접속매매) 거래가 여기 들어오는지는 장 뒤에 실측해서
    확인한다. 값이 0 이면 비운다.
    """
    def one(code: str):
        try:
            o = kis.get(
                "/uapi/domestic-stock/v1/quotations/inquire-overtime-price", "FHPST02300000",
                {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
            ).get("output") or {}
        except Exception as exc:
            log.debug("시간외 실패 %s: %s", code, exc)
            return code, None
        price = _num(o.get("ovtm_untp_prpr"))
        if price <= 0:
            return code, None
        return code, {
            "price": price,
            "volume": _num(o.get("ovtm_untp_vol")),
            "amount_eok": round(_num(o.get("ovtm_untp_tr_pbmn")) / 1e8, 1),
        }

    kis.token
    return _parallel(one, codes)


def flows(kis: KisClient, codes: list[str], date: str) -> dict[str, dict]:
    """외국인·기관 당일 + 최근 5거래일 누적. 15:40 전에는 API 가 막혀 비어 온다."""
    def one(code: str):
        try:
            rows = kis.investor_trade_by_stock_daily(code, date)
        except Exception as exc:
            log.debug("수급 실패 %s: %s", code, exc)
            return code, None
        rows = sorted(rows or [], key=lambda r: r.get("stck_bsop_date", ""), reverse=True)
        if not rows or rows[0].get("stck_bsop_date") != date:
            return code, None
        eok = lambda r, k: _num(r.get(k)) / 100   # 백만원 -> 억
        last5 = rows[:5]
        return code, {
            "frgn_eok": round(eok(rows[0], "frgn_ntby_tr_pbmn"), 1),
            "orgn_eok": round(eok(rows[0], "orgn_ntby_tr_pbmn"), 1),
            "frgn_5d_eok": round(sum(eok(r, "frgn_ntby_tr_pbmn") for r in last5), 1),
            "orgn_5d_eok": round(sum(eok(r, "orgn_ntby_tr_pbmn") for r in last5), 1),
        }

    kis.token
    return _parallel(one, codes)


def merge(uni: list[dict], q: dict, ah: dict, fl: dict) -> list[dict]:
    """종목 표 한 벌. 시세가 없는 종목(거래정지 등)은 뺀다."""
    out = []
    for s in uni:
        if s["code"] not in q:
            continue
        row = {**s, **q[s["code"]]}
        a = ah.get(s["code"])
        if a:
            # 정규장 종가 대비. KIS 의 전일대비 필드는 무엇 대비인지 문서가 모호해서 직접 잰다.
            row["ah_price"] = a["price"]
            row["ah_chg_pct"] = round((a["price"] / row["price"] - 1) * 100, 2)
            row["ah_amount_eok"] = a["amount_eok"]
        row.update(fl.get(s["code"], {}))
        out.append(row)
    return out


def group_summary(stocks: list[dict]) -> list[dict]:
    """묶음별 평균 등락·오른 종목 수·거래대금·수급 합."""
    out = []
    for group, _ in GROUPS:
        rows = [s for s in stocks if s["group"] == group]
        if not rows:
            continue
        g = {
            "name": group,
            "count": len(rows),
            "avg_chg_pct": round(sum(r["chg_pct"] for r in rows) / len(rows), 2),
            "up": sum(1 for r in rows if r["chg_pct"] > 0),
            "down": sum(1 for r in rows if r["chg_pct"] < 0),
            "amount_eok": round(sum(r["amount_eok"] for r in rows), 1),
        }
        if any("frgn_eok" in r for r in rows):
            g["frgn_eok"] = round(sum(r.get("frgn_eok", 0) for r in rows), 1)
            g["orgn_eok"] = round(sum(r.get("orgn_eok", 0) for r in rows), 1)
        ah = [r["ah_chg_pct"] for r in rows if "ah_chg_pct" in r]
        if ah:
            g["ah_avg_chg_pct"] = round(sum(ah) / len(ah), 2)
        out.append(g)
    return out


# ---------------------------------------------------------------- 분석
def _obj(props: dict, required: list[str]) -> dict:
    # 구조화 출력은 모든 객체에 additionalProperties: False 를 요구한다(뉴스 첫 실행 실패).
    return {"type": "object", "properties": props, "required": required,
            "additionalProperties": False}


_USED = {"type": "array", "items": _obj(
    {"index": {"type": "integer"}, "why": {"type": "string"}}, ["index", "why"])}
_STR_LIST = {"type": "array", "items": {"type": "string"}}

EVENING_SCHEMA = _obj({
    "headline": {"type": "string"},
    "points": _STR_LIST,
    "call": _obj({
        "direction": {"type": "string", "enum": DIRECTIONS},
        "confidence": {"type": "string", "enum": CONFIDENCE},
        "reason": {"type": "string"},
    }, ["direction", "confidence", "reason"]),
    "scenarios": {"type": "array", "items": _obj(
        {"if": {"type": "string"}, "then": {"type": "string"}}, ["if", "then"])},
    "watch": _STR_LIST,
    "used": _USED,
}, ["headline", "points", "call", "scenarios", "watch", "used"])

MORNING_SCHEMA = _obj({
    "headline": {"type": "string"},
    "points": _STR_LIST,
    "revise": {"type": "string"},
    "watch": _STR_LIST,
    "used": _USED,
}, ["headline", "points", "revise", "watch", "used"])


def _eok(v: float) -> str:
    return f"{v:+,.0f}억"


def _table(stocks: list[dict], groups: list[dict]) -> str:
    lines = []
    for g in groups:
        head = (f"[{g['name']}] {g['count']}종목 · 평균 {g['avg_chg_pct']:+.2f}% "
                f"(상승 {g['up']} / 하락 {g['down']}) · 거래대금 {g['amount_eok']:,.0f}억")
        if "frgn_eok" in g:
            head += f" · 외국인 {_eok(g['frgn_eok'])} · 기관 {_eok(g['orgn_eok'])}"
        if "ah_avg_chg_pct" in g:
            head += f" · 애프터마켓 평균 {g['ah_avg_chg_pct']:+.2f}%"
        lines.append(head)
        for s in stocks:
            if s["group"] != g["name"]:
                continue
            row = (f"  {s['name']} {s['price']:,.0f}원 {s['chg_pct']:+.2f}% "
                   f"거래대금 {s['amount_eok']:,.0f}억")
            if "frgn_eok" in s:
                row += (f" · 외국인 {_eok(s['frgn_eok'])}(5일 {_eok(s['frgn_5d_eok'])})"
                        f" · 기관 {_eok(s['orgn_eok'])}(5일 {_eok(s['orgn_5d_eok'])})")
            if "ah_chg_pct" in s:
                row += f" · 애프터마켓 {s['ah_chg_pct']:+.2f}%"
            lines.append(row)
    return "\n".join(lines)


def _listing(articles: list[dict]) -> str:
    return "\n".join(
        f"{i}. [{a['time']}] ({a['source']}) {a['title']}" for i, a in enumerate(articles, 1)
    )


_RULES = """지켜야 할 것.
- 기사는 제목만 있다. 제목이 말하는 범위를 넘는 사실을 지어내지 않는다.
- 숫자는 표나 기사 제목에 있는 것만 쓴다. 증권사 목표가처럼 제목에 적힌 숫자는 인용해도 되지만,
  어디에도 없는 숫자를 만들지 않는다.
- 안전 캠페인·봉사·협약식 같은 회사 홍보성 기사는 근거로 고르지 않는다.
- 이유를 댈 때는 기사나 수급 숫자에 기대고, 근거가 없으면 '뚜렷한 재료는 보이지 않는다' 고 쓴다.
- used 에는 근거로 쓴 기사 번호를 중요한 순으로 최대 {n}개. 번호는 1~{m} 사이 실제 번호.
- 한국어로 쓰고 문장은 마침표로 끝낸다."""


def _evening_prompt(day, stocks, groups, articles) -> str:
    return f"""{day} 한국 조선·조선기자재·해운·LNG 종목의 오늘 장을 정리한다.
정규장은 15:30 에 끝났고 애프터마켓(16:00~20:00)까지 마친 뒤다.

[종목 표]
{_table(stocks, groups)}

[오늘 기사 제목 {len(articles)}건]
{_listing(articles)}

할 일.
1) headline — 오늘 조선주를 한 문장으로. 40자 안팎.
2) points — 4~6개. 근황과 오르거나 내린 이유. 묶음(조선/기자재/해운/LNG) 사이
   온도차가 있으면 짚는다. 수급(외국인·기관 당일과 5일 누적)을 이유와 연결한다.
3) call — 다음 거래일 조선 묶음의 방향.
   direction 은 상승/하락/보합 중 하나, confidence 는 높음/보통/낮음, reason 은 한두 문장.
   제목만 보고 내리는 판단이라 대개 '보통' 이나 '낮음' 이 맞다. '높음' 은 근거가 겹칠 때만.
4) scenarios — 2~3개. '이러면(if) → 이렇게 된다(then)'.
5) watch — 다음 거래일에 볼 변수 3~5개. 짧게.

{_RULES.format(n=SOURCES_N, m=len(articles))}"""


def _morning_prompt(day, stocks, groups, articles, last_evening) -> str:
    prev = "(전날 저녁 분석 없음)"
    if last_evening:
        c = last_evening.get("call") or {}
        prev = (f"날짜 {last_evening.get('date')}\n"
                f"한 줄: {last_evening.get('headline')}\n"
                f"전망: {c.get('direction')} (확신도 {c.get('confidence')}) — {c.get('reason')}\n"
                "요점:\n" + "\n".join(f"- {p}" for p in last_evening.get("points") or []))
    return f"""{day} 아침. 한국 조선·조선기자재·해운·LNG 종목을 장 시작 전에 정리한다.
시세는 전일 종가 기준이다(아직 장이 안 열렸다).

[전날 저녁 분석]
{prev}

[종목 표 — 전일 종가]
{_table(stocks, groups)}

[밤사이 기사 제목 {len(articles)}건]
{_listing(articles)}

할 일.
1) headline — 오늘 아침 조선주를 한 문장으로. 40자 안팎.
2) points — 3~5개. 밤사이 달라진 것 위주. 미국·유가·운임·수주 소식이 있으면 짚는다.
3) revise — 전날 저녁 전망을 바꿀 만한 소식이 있는지 한두 문장. 없으면 '전망을 바꿀 소식은 없다' 고 쓴다.
4) watch — 오늘 장에서 볼 포인트 3~5개. 짧게.

{_RULES.format(n=SOURCES_N, m=len(articles))}"""


def map_sources(pool: list[dict], used: list[dict]) -> list[dict]:
    """모델이 고른 번호를 실제 기사로. 범위 밖·중복·정수 아닌 번호는 버린다."""
    out, seen = [], set()
    for item in used or []:
        i = item.get("index")
        if not isinstance(i, int) or isinstance(i, bool) or not (1 <= i <= len(pool)) or i in seen:
            continue
        seen.add(i)
        out.append({**pool[i - 1], "why": (item.get("why") or "").strip()})
    return out[:SOURCES_N]


def _since(slot: str, now: datetime, last_evening: dict | None = None) -> datetime:
    """기사 창.

    저녁은 오늘 00:00 부터. 아침은 마지막 저녁 분석 시각부터 — '전날 20:00' 으로
    고정하면 월요일 아침에 토·일 기사가 빠진다. 저녁 분석이 없거나 너무 오래됐으면
    전날 20:00 부터.
    """
    if slot == "evening":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    fallback = (now - timedelta(days=1)).replace(hour=20, minute=0, second=0, microsecond=0)
    try:
        last = datetime.fromisoformat((last_evening or {})["generated_at"])
    except (KeyError, TypeError, ValueError):
        return fallback
    return last if now - last <= timedelta(days=4) else fallback


def build(slot: str, last_evening: dict | None = None) -> dict | None:
    if slot not in ("morning", "evening"):
        raise ValueError(slot)

    now = datetime.now(config.KST)
    day = now.strftime("%Y-%m-%d")
    ymd = now.strftime("%Y%m%d")

    uni = universe()
    log.info("조선 탭 종목 %d개", len(uni))
    codes = [s["code"] for s in uni]

    kis = KisClient()
    q = quotes(kis, codes)
    ah = after_hours(kis, codes) if slot == "evening" else {}
    fl = flows(kis, codes, ymd) if slot == "evening" else {}
    log.info("시세 %d · 애프터마켓 %d · 수급 %d", len(q), len(ah), len(fl))

    stocks = merge(uni, q, ah, fl)
    if not stocks:
        log.error("조선 종목 시세를 하나도 못 받았습니다.")
        return None
    groups = group_summary(stocks)

    articles, window = news.gather(
        _since(slot, now, last_evening), queries=QUERIES, keep=keep
    )
    pool = articles[:POOL]
    log.info("조선 기사 %d건 (모델에 %d건)", len(articles), len(pool))

    prompt = (_evening_prompt(day, stocks, groups, pool) if slot == "evening"
              else _morning_prompt(day, stocks, groups, pool, last_evening))
    schema = EVENING_SCHEMA if slot == "evening" else MORNING_SCHEMA
    try:
        got = _ask(prompt, schema)
    except Exception as exc:
        log.warning("조선 분석 실패: %s", exc)
        return None
    if not got:
        return None

    report = {
        "slot": slot,
        "date": day,
        "generated_at": now.isoformat(timespec="seconds"),
        "window": window,
        "collected": len(articles),
        "pool": len(pool),
        "has_after_hours": bool(ah),
        "has_flows": bool(fl),
        "groups": groups,
        "stocks": stocks,
        "headline": (got.get("headline") or "").strip(),
        "points": [p.strip() for p in got.get("points") or [] if p.strip()],
        "watch": [w.strip() for w in got.get("watch") or [] if w.strip()],
        "sources": map_sources(pool, got.get("used")),
    }
    if slot == "evening":
        report["call"] = got.get("call") or {}
        report["scenarios"] = got.get("scenarios") or []
    else:
        report["revise"] = (got.get("revise") or "").strip()
    return report


def load(path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
