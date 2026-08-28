"""
증시가 왜 움직였는지에 대한 의견.

두 단계로 나눈다.

  1) 오늘 움직임이 최근 기준으로 큰가  — 파이썬이 판정한다
  2) 그렇다면 왜 그랬을까              — 기사와 우리 데이터를 함께 넣고 모델에게 묻는다

1번을 모델에게 맡기지 않는 이유가 있다. 고정 임계값은 시장마다 뜻이 달라진다.
실측(2026-08-28)으로 코스피의 최근 20영업일 표준편차는 4.91%, S&P 500 은 0.65%다.
'1% 하락' 에 같은 무게를 두면 코스피는 62% 의 날이, S&P 는 20% 의 날이 걸린다.
그래서 등락률을 그 시장의 표준편차로 나눈 값(z)으로 판정하고, 결과를 모델에게
알려준다. 같은 날을 다시 돌려도 같은 판정이 나온다.

표준편차는 야후 일봉으로 잰다. 우리 백필 데이터에는 과거 지수가 없다(과거 지수를
돌려주는 API 가 없어 그날치만 채운다 — collect.py 참고).

키가 없거나 호출이 실패하면 이 꼭지만 통째로 빠지고 리포트는 그대로 나간다.
"""
from __future__ import annotations

import json
import logging
import statistics
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

import config

log = logging.getLogger(__name__)

MODEL = "claude-sonnet-5"

# z = 오늘 등락률 / 최근 20영업일 일간수익률 표준편차
SIGMA_WINDOW = 20
BANDS = (
    (0.5, "quiet", "조용"),
    (1.0, "normal", "보통"),
    (2.0, "big", "큰 날"),
    (float("inf"), "extreme", "이례적"),
)

MARKETS = {
    "kr": {
        "index": "코스피",
        "symbol": "^KS11",
        "queries": ["코스피 마감", "코스피 외국인 순매수", "코스피 시황"],
    },
    "us": {
        "index": "S&P 500",
        "symbol": "^GSPC",
        # 국내 언론의 뉴욕증시 마감 기사를 쓴다. 읽는 사람도 한국어고,
        # 간밤 미국장을 한국 시각 아침에 정리한 기사가 이 리포트와 시점이 맞는다.
        "queries": ["뉴욕증시 마감", "나스닥 마감", "미국 증시 마감"],
    },
}

MAX_HEADLINES = 40

# 절대 기준. σ 와 다른 질문에 답한다 — "요즘 기준으로 특이한가" 가 아니라
# "오늘 얼마나 흔들렸나". 통념(±3% 급등락)과 거래소 안전장치 발동선을 참고선으로 쓴다.
#
# 지금 코스피에 대면 ±3% 는 4일에 한 번 걸린다(최근 243거래일 중 26%). 그래서 이걸
# 해설을 붙일지 말지의 기준으로는 쓰지 않는다. 다만 '절대로는 큰 폭인데 요즘 기준으론
# 평범' 같은 날을 짚어 주려면 둘 다 있어야 한다.
#
# 사이드카는 선물 ±5%, 서킷브레이커는 현물 하락 8/15/20% 가 실제 요건이다. 여기서는
# 현물 등락률만 보므로 '발동' 이 아니라 '그 근처' 라고만 말한다.
ABS_BANDS = (
    (1.5, "보통"),
    (3.0, "다소 큼"),
    (5.0, "급등락"),
    (8.0, "사이드카 발동선 부근"),
    (float("inf"), "서킷브레이커 발동선 부근"),
)

# VKOSPI. 통상 30 을 넘으면 시장이 크게 흔들리는 국면으로 본다.
VKOSPI_BANDS = ((20, "안정"), (30, "경계"), (40, "공포"), (float("inf"), "극심한 공포"))


# ---------------------------------------------------------------- 판정
def _yahoo_closes(symbol: str, rng: str = "3mo") -> list[float]:
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"range": rng, "interval": "1d"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        r.raise_for_status()
        res = r.json()["chart"]["result"][0]
        return [c for c in res["indicators"]["quote"][0]["close"] if c is not None]
    except Exception as exc:
        log.warning("야후 일봉 실패 (%s): %s", symbol, exc)
        return []


def sigma(symbol: str, window: int = SIGMA_WINDOW) -> float | None:
    """최근 window 영업일 일간수익률의 표준편차(%)."""
    closes = _yahoo_closes(symbol)
    if len(closes) < window + 1:
        return None
    rets = [
        (closes[i] - closes[i - 1]) / closes[i - 1] * 100
        for i in range(len(closes) - window, len(closes))
    ]
    s = statistics.pstdev(rets)
    return round(s, 3) if s > 0 else None


def classify(chg_pct: float, sig: float | None) -> dict:
    """등락률을 그 시장의 변동성으로 나눠 구간을 정한다."""
    if not sig:
        return {"z": None, "band": "unknown", "label": "판정 불가", "sigma": None}
    z = abs(chg_pct) / sig
    for edge, band, label in BANDS:
        if z < edge:
            break
    return {"z": round(z, 2), "band": band, "label": label, "sigma": sig}


def abs_band(chg_pct: float) -> str:
    """통념·제도 기준으로 본 오늘의 크기. 하락 8% 이상만 서킷 문구를 쓴다."""
    a = abs(chg_pct)
    for edge, label in ABS_BANDS:
        if a < edge:
            break
    if label == "서킷브레이커 발동선 부근" and chg_pct > 0:
        return "급등 (서킷 요건은 하락만)"
    return label


def vkospi_band(value: float | None) -> str:
    if value is None:
        return ""
    for edge, label in VKOSPI_BANDS:
        if value < edge:
            break
    return label


def flow_z(report: dict, history: list[dict]) -> float | None:
    """외국인+기관 순매수가 최근 대비 얼마나 이례적인가.

    지수는 조용한데 수급만 크게 움직인 날을 잡으려고 둔다. 뉴스는 지수만 보고 쓴다.
    """
    def net(rep: dict) -> float | None:
        iv = rep.get("investors") or {}
        f, i = iv.get("foreign_eok"), iv.get("institution_eok")
        if f is None and i is None:
            return None
        return (f or 0) + (i or 0)

    today = net(report)
    past = [v for v in (net(h) for h in history) if v is not None]
    if today is None or len(past) < 5:
        return None
    s = statistics.pstdev(past)
    return round(today / s, 2) if s > 0 else None


# ---------------------------------------------------------------- 기사
def headlines(queries: list[str], on_date: str) -> list[dict]:
    """구글 뉴스 RSS 로 그날 기사 제목을 모은다. 본문은 긁지 않는다."""
    seen: dict[str, dict] = {}
    for q in queries:
        url = (
            "https://news.google.com/rss/search?q="
            + urllib.parse.quote(q)
            + "&hl=ko&gl=KR&ceid=KR:ko"
        )
        try:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
            r.raise_for_status()
            root = ET.fromstring(r.content)
        except Exception as exc:
            log.warning("뉴스 조회 실패 (%s): %s", q, exc)
            continue

        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            if not title or title in seen:
                continue
            try:
                when = parsedate_to_datetime(item.findtext("pubDate") or "")
                kst = when.astimezone(config.KST)
            except Exception:
                continue
            if kst.strftime("%Y-%m-%d") != on_date:
                continue   # 그날 기사만. 지난 기사가 섞이면 엉뚱한 이유가 붙는다
            seen[title] = {
                "title": title,
                "source": (item.findtext("source") or "").strip(),
                "time": kst.strftime("%H:%M"),
                "url": (item.findtext("link") or "").strip(),
            }

    rows = sorted(seen.values(), key=lambda x: x["time"])
    return rows[:MAX_HEADLINES]


# ---------------------------------------------------------------- 프롬프트
SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string"},
        "points": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["높음", "보통", "낮음"]},
        "conflict": {"type": "string"},
        "used": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["verdict", "points", "confidence", "conflict", "used"],
    "additionalProperties": False,
}

RULES = """규칙
1. 구간이 '조용' 이면 특별한 이유가 없다는 것이 기본 답이다. 기사가 이유를 붙이더라도
   최근 변동성 기준으로 평범한 움직임이면 그렇게 말하라. 없는 이유를 만들지 마라.
2. verdict 는 화면 맨 위에 굵게 한 줄로 들어간다. **한 문장, 공백 포함 100자 이내.**
   결론만 담는다. 근거·숫자는 points 에 쓴다. 여러 문장을 이어 붙이지 마라.
3. points 는 3~5개. **각 150자 이내.** verdict 에 쓴 말을 되풀이하지 마라. 각 항목이
   데이터에 근거한 것인지 기사에 근거한 것인지 드러나게 쓴다.
4. 기사 주장이 우리 데이터와 어긋나면 conflict 에 적어라. 예: 기사는 반도체 매도라는데
   반도체 테마가 순매수 1위인 경우. 어긋나는 게 없으면 빈 문자열.
5. 단정하지 마라. '~로 보인다', '~라는 설명이 많다' 처럼 쓴다. 투자 권유는 하지 마라.
6. used 에는 실제로 근거로 삼은 기사 번호만 **최대 5개**. 읽은 기사를 다 나열하지 마라.
7. confidence 는 데이터와 기사가 같은 방향을 가리킬수록 높다. 구간이 '조용' 이면 '낮음'.
8. 두 기준이 어긋나면 그 사실 자체를 말하라. 예: 절대로는 3% 넘게 빠졌지만 요즘 이
   시장에서는 평범한 등락이다. 어느 한쪽만 보고 쓰지 마라.
9. 한국어로 쓴다. 지수·종목·기관 이름(코스피, S&P 500, 나스닥, VIX, 엔비디아,
   SK하이닉스, AI 처럼 굳어진 말)은 그대로 쓰되, **그 밖의 영어 낱말은 쓰지 마라.**
   특히 영어 동사에 한국어 어미를 붙이지 마라 — 'diverge했다' 같은 표현은 금지다.
   증권가에서 흔한 외래어도 우리말로 바꿔 쓴다.
     디커플링 → 따로 움직임 / 엇갈림      서프라이즈 → 깜짝 실적
     가이던스 → 실적 전망                랠리 → 상승세
     모멘텀 → 흐름                       센티멘트 → 투자 심리
     리스크 → 위험                       바스켓 → 묶음"""


def _fmt_report(report: dict, verdict: dict, fz: float | None, market: str) -> str:
    lines = []
    idx = {i["name"]: i for i in (report.get("indices") or [])}
    us = report.get("us") or {}

    if market == "kr":
        for name in ("코스피", "코스닥"):
            i = idx.get(name)
            if i:
                lines.append(
                    f"{name} {i['value']:,.2f} ({i['chg_pct']:+.2f}%) "
                    f"상승 {i.get('up', 0)} / 하락 {i.get('down', 0)}"
                )
        iv = report.get("investors") or {}
        lines.append(
            f"외국인 {iv.get('foreign_eok', 0):+,.0f}억 · "
            f"기관 {iv.get('institution_eok', 0):+,.0f}억"
            + (f" (수급 z={fz})" if fz is not None else "")
        )
        tops = report.get("themes_top") or []
        bots = report.get("themes_bottom") or []
        if tops:
            lines.append("자금 유입 테마: " + ", ".join(
                f"{t['name']} {t['net_eok']:+,.0f}억" for t in tops[:5]))
        if bots:
            lines.append("자금 이탈 테마: " + ", ".join(
                f"{t['name']} {t['net_eok']:+,.0f}억" for t in bots[:3]))
        sec = report.get("sectors") or []
        if sec:
            hot = sorted(sec, key=lambda s: s["chg_pct"], reverse=True)
            lines.append(
                "업종 상위: " + ", ".join(f"{s['name']} {s['chg_pct']:+.2f}%" for s in hot[:3])
                + " / 하위: " + ", ".join(f"{s['name']} {s['chg_pct']:+.2f}%" for s in hot[-3:])
            )
        if us.get("indices"):
            lines.append("간밤 미국: " + ", ".join(
                f"{i['name']} {i['chg_pct']:+.2f}%" for i in us["indices"]))
        if us.get("sectors"):
            s = us["sectors"]
            lines.append(f"미국 섹터 1위 {s[0]['name']} {s[0]['chg_pct']:+.2f}% / "
                         f"꼴찌 {s[-1]['name']} {s[-1]['chg_pct']:+.2f}%")
    else:
        for i in us.get("indices") or []:
            lines.append(f"{i['name']} {i['value']:,.2f} ({i['chg_pct']:+.2f}%)")
        for m in us.get("macro") or []:
            lines.append(f"{m['name']} {m['value']} ({m['chg_pct']:+.2f}%)")
        s = us.get("sectors") or []
        if s:
            lines.append("섹터 상위: " + ", ".join(f"{x['name']} {x['chg_pct']:+.2f}%" for x in s[:3])
                         + " / 하위: " + ", ".join(f"{x['name']} {x['chg_pct']:+.2f}%" for x in s[-3:]))
        st = us.get("stocks") or []
        if st:
            lines.append("종목 상위: " + ", ".join(f"{x['name']} {x['chg_pct']:+.2f}%" for x in st[:5]))
        for e in us.get("extras") or []:
            lines.append(f"{e['name']} {e['value']} ({e['chg_pct']:+.2f}%)")

    return "\n".join(lines)


def _prompt(report: dict, verdict: dict, fz, news: list[dict], market: str) -> str:
    spec = MARKETS[market]
    rel = (
        f"상대 기준 — 최근 {SIGMA_WINDOW}영업일 표준편차({verdict['sigma']}%)의 "
        f"{verdict['z']}배. 구간: {verdict['label']}."
        if verdict["z"] is not None
        else "상대 기준 — 계산하지 못했다."
    )
    absolute = f"절대 기준 — 통념·제도 기준으로는 '{verdict['abs_label']}'."
    vk = verdict.get("vkospi")
    vk_line = (
        f"VKOSPI {vk['value']} ({vk['chg_pct']:+.2f}%) — {vkospi_band(vk['value'])} 구간."
        if vk else ""
    )
    band_line = "\n".join(x for x in (rel, absolute, vk_line) if x)
    articles = "\n".join(
        f"{n}. [{a['time']}] {a['title']}" + (f" ({a['source']})" if a["source"] else "")
        for n, a in enumerate(news, 1)
    ) or "(그날 기사를 찾지 못했다)"

    return f"""너는 한국 개인투자자가 보는 수급 리포트의 '왜 움직였나' 꼭지를 쓴다.

[오늘 판정 — 이미 계산된 값이다. 다시 판단하지 마라]
{band_line}

[오늘 데이터]
{_fmt_report(report, verdict, fz, market)}

[오늘 기사 제목 {len(news)}건]
{articles}

{RULES}"""


# ---------------------------------------------------------------- 실행
def _ask(prompt: str) -> dict | None:
    import anthropic

    client = anthropic.Anthropic()
    res = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": prompt}],
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
    )
    u = res.usage
    log.info(
        "  토큰 입력 %d · 출력 %d · 약 $%.4f",
        u.input_tokens, u.output_tokens,
        u.input_tokens / 1e6 * 2.0 + u.output_tokens / 1e6 * 10.0,   # Sonnet 5 단가
    )

    text = next((b.text for b in res.content if b.type == "text"), "")
    return json.loads(text) if text else None


def _one(report: dict, history: list[dict], market: str) -> dict | None:
    spec = MARKETS[market]

    if market == "kr":
        idx = {i["name"]: i for i in (report.get("indices") or [])}
        row = idx.get("코스피")
        chg = row["chg_pct"] if row else None
    else:
        us = report.get("us") or {}
        row = next((i for i in (us.get("indices") or []) if i["name"] == "S&P 500"), None)
        chg = row["chg_pct"] if row else None

    if chg is None:
        log.info("%s 지수가 없어 해설을 건너뜁니다.", spec["index"])
        return None

    verdict = classify(chg, sigma(spec["symbol"]))
    verdict["abs_label"] = abs_band(chg)
    verdict["vkospi"] = report.get("vkospi") if market == "kr" else None
    fz = flow_z(report, history) if market == "kr" else None
    news = headlines(spec["queries"], report["date"])

    out = {
        "index": spec["index"],
        "chg_pct": chg,
        "sigma": verdict["sigma"],
        "z": verdict["z"],
        "band": verdict["band"],
        "label": verdict["label"],
        "abs_label": verdict["abs_label"],
        "vkospi": verdict["vkospi"],
        "vkospi_band": vkospi_band((verdict["vkospi"] or {}).get("value")),
        "flow_z": fz,
        "sources": news,
    }

    if not news:
        # 기사가 없으면 판정만 남긴다. 데이터만으로 이유를 지어내게 두지 않는다.
        log.info("%s: 그날 기사를 찾지 못해 판정만 남깁니다.", spec["index"])
        return out

    try:
        got = _ask(_prompt(report, verdict, fz, news, market))
    except Exception as exc:
        log.warning("%s 해설 생성 실패: %s", spec["index"], exc)
        return out

    if got:
        out.update(
            {
                "verdict": got.get("verdict", ""),
                "points": got.get("points") or [],
                "confidence": got.get("confidence", ""),
                "conflict": got.get("conflict", ""),
                "used": got.get("used") or [],
            }
        )
        log.info(
            "%s 해설: %s (상대 %s · 절대 %s · 확신 %s · 기사 %d건)",
            spec["index"], out["verdict"][:40], out["label"],
            out["abs_label"], out["confidence"], len(news),
        )
    return out


def explain(report: dict, history: list[dict]) -> dict | None:
    """한국·미국 각각 한 번씩. 실패한 쪽만 빠진다."""
    if not config.ANTHROPIC_API_KEY:
        log.info("ANTHROPIC_API_KEY 가 없어 증시 해설을 건너뜁니다.")
        return None

    out: dict = {"model": MODEL, "markets": {}}
    for market in ("kr", "us"):
        try:
            got = _one(report, history, market)
        except Exception as exc:
            log.warning("%s 해설 실패(무시): %s", market, exc)
            continue
        if got:
            out["markets"][market] = got

    return out if out["markets"] else None
