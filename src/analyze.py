"""
수집한 스냅샷을 '읽을 수 있는 리포트' 로 바꾼다.

핵심 산출물
  1. investors  : 시장 전체 투자자별 순매수 (외국인/기관/개인)
  2. themes     : 테마별 수급 롤업 (순매수 대금, 참여 폭, 평균 등락률)
  3. rotation   : 전 거래일 대비 테마 간 자금 이동 (추정)
  4. spikes     : 거래대금이 평소보다 크게 튄 종목
  5. headline   : 한 줄 요약 문장들

※ rotation 은 '어제 A테마에서 빠진 금액과 오늘 B테마로 들어온 금액' 을
   크기순으로 짝지은 추정이다. 실제로 같은 돈이 옮겨갔다는 증거는 아니다.
"""
from __future__ import annotations

import logging
from typing import Any

import config

log = logging.getLogger(__name__)

EOK = 1e8  # 1억원


def to_eok(won: float) -> float:
    return round(won / EOK, 1)


# ---------------------------------------------------------------- 투자자별
def summarize_investors(snapshot: dict) -> dict:
    frgn = sum(r["frgn"] for r in snapshot["flows"])
    orgn = sum(r["orgn"] for r in snapshot["flows"])
    prsn = sum(r.get("prsn", 0.0) for r in snapshot["flows"])
    has_retail = any("prsn" in r for r in snapshot["flows"])

    return {
        "foreign_eok": to_eok(frgn),
        "institution_eok": to_eok(orgn),
        "retail_eok": to_eok(prsn) if has_retail else None,
        # 개인 수급이 없으면 외국인+기관의 반대편으로 추정한다
        "retail_estimated": not has_retail,
        "retail_proxy_eok": to_eok(-(frgn + orgn)) if not has_retail else None,
    }


# ---------------------------------------------------------------- 테마 롤업
def rollup_themes(
    snapshot: dict,
    themes: dict[str, list[str]],
    *,
    min_members: int | None = None,
) -> list[dict]:
    min_members = config.MIN_THEME_MEMBERS if min_members is None else min_members
    by_code = {r["code"]: r for r in snapshot["flows"]}

    result: list[dict] = []
    for name, codes in themes.items():
        members = [by_code[c] for c in codes if c in by_code]
        active = [m for m in members if m["net"] != 0]
        if len(active) < min_members:
            continue

        net = sum(m["net"] for m in members)
        frgn = sum(m["frgn"] for m in members)
        orgn = sum(m["orgn"] for m in members)
        buys = sum(1 for m in active if m["net"] > 0)
        chgs = [m["chg_pct"] for m in members if m["chg_pct"]]

        ranked = sorted(members, key=lambda m: -abs(m["net"]))
        top = ranked[: config.TOP_STOCKS_PER_THEME]

        # 집중도: 1등 종목이 테마 수급에서 차지하는 비중.
        # 분모를 순매수 합이 아니라 절대값 합으로 두는 이유 — 매수/매도가 섞여
        # 합이 0 에 가까워지면 비중이 무한대로 튀기 때문이다.
        abs_sum = sum(abs(m["net"]) for m in active)
        top1_share = round(abs(ranked[0]["net"]) / abs_sum, 3) if abs_sum else 0.0

        result.append(
            {
                "name": name,
                "net_eok": to_eok(net),
                "foreign_eok": to_eok(frgn),
                "institution_eok": to_eok(orgn),
                "members_total": len(codes),
                "members_with_data": len(active),
                # 참여 폭: 수급이 잡힌 종목 중 순매수인 비율 (한 종목이 끌고 가는지 판별)
                "breadth": round(buys / len(active), 2) if active else 0.0,
                "top1_share": top1_share,
                "top1_name": ranked[0]["name"] if ranked else "",
                # 구성종목 중 실제로 수급이 잡힌 비율 (읽을 때 표본 크기 감을 주려고)
                "coverage": round(len(active) / len(codes), 3) if codes else 0.0,
                # 상단 노출 자격. 아래 analyze() 에서 themes_top/bottom 을 고를 때 쓴다.
                "featured": (
                    top1_share < config.THEME_MAX_TOP1_SHARE
                    and len(active) >= config.THEME_MIN_DATA_MEMBERS
                ),
                "avg_chg_pct": round(sum(chgs) / len(chgs), 2) if chgs else 0.0,
                "stocks": [
                    {
                        "code": m["code"],
                        "name": m["name"],
                        "net_eok": to_eok(m["net"]),
                        "foreign_eok": to_eok(m["frgn"]),
                        "institution_eok": to_eok(m["orgn"]),
                        "chg_pct": m["chg_pct"],
                    }
                    for m in top
                ],
            }
        )

    result.sort(key=lambda t: -t["net_eok"])
    return result


# ---------------------------------------------------------------- 자금 이동
def compute_rotation(
    today: list[dict], previous: list[dict] | None, *, top_n: int = 5
) -> dict:
    """전 거래일 대비 테마별 순매수 증감으로 자금 이동을 추정한다."""
    if not previous:
        return {"available": False, "inflow": [], "outflow": [], "pairs": [], "note": ""}

    prev_map = {t["name"]: t["net_eok"] for t in previous}
    deltas = []
    for t in today:
        prev = prev_map.get(t["name"])
        if prev is None:
            continue
        deltas.append(
            {
                "name": t["name"],
                "today_eok": t["net_eok"],
                "prev_eok": prev,
                "delta_eok": round(t["net_eok"] - prev, 1),
            }
        )

    if not deltas:
        return {"available": False, "inflow": [], "outflow": [], "pairs": [], "note": ""}

    deltas.sort(key=lambda d: -d["delta_eok"])
    inflow = [d for d in deltas if d["delta_eok"] > 0][:top_n]
    outflow = [d for d in deltas if d["delta_eok"] < 0][-top_n:][::-1]

    # 빠져나간 쪽 금액을 들어온 쪽에 큰 것부터 그리디하게 배분한다
    pairs: list[dict] = []
    src = [[d["name"], -d["delta_eok"]] for d in outflow]
    dst = [[d["name"], d["delta_eok"]] for d in inflow]
    i = j = 0
    while i < len(src) and j < len(dst):
        amount = min(src[i][1], dst[j][1])
        if amount > 0:
            pairs.append(
                {"from": src[i][0], "to": dst[j][0], "amount_eok": round(amount, 1)}
            )
        src[i][1] -= amount
        dst[j][1] -= amount
        if src[i][1] <= 0.05:
            i += 1
        if dst[j][1] <= 0.05:
            j += 1

    pairs.sort(key=lambda p: -p["amount_eok"])
    return {
        "available": True,
        "inflow": inflow,
        "outflow": outflow,
        "pairs": pairs[:8],
        "note": "전 거래일 대비 테마별 순매수 증감을 크기순으로 짝지은 추정입니다. "
        "같은 자금이 실제로 이동했다는 뜻은 아닙니다.",
    }


def compute_streaks(today: list[dict], history: list[list[dict]]) -> dict[str, int]:
    """테마별 연속 순매수/순매도 일수. history 는 최신순 리스트."""
    streaks: dict[str, int] = {}
    for t in today:
        sign = 1 if t["net_eok"] > 0 else -1 if t["net_eok"] < 0 else 0
        if sign == 0:
            streaks[t["name"]] = 0
            continue
        run = 1
        for past in history:
            prev = next((p for p in past if p["name"] == t["name"]), None)
            if prev is None:
                break
            prev_sign = 1 if prev["net_eok"] > 0 else -1 if prev["net_eok"] < 0 else 0
            if prev_sign != sign:
                break
            run += 1
        streaks[t["name"]] = run * sign
    return streaks


# ---------------------------------------------------------------- 거래대금 급증
def find_spikes(snapshot: dict, stock_themes: dict[str, list[str]]) -> list[dict]:
    leaders = snapshot.get("volume_leaders") or []
    out = []
    for r in leaders:
        inc = r.get("vol_increase_pct") or 0.0
        if inc < (config.VOLUME_SPIKE_RATIO - 1) * 100:
            continue
        out.append(
            {
                "code": r["code"],
                "name": r["name"],
                "chg_pct": r["chg_pct"],
                "amount_eok": to_eok(r["amount"]),
                "vol_increase_pct": round(inc, 1),
                "themes": stock_themes.get(r["code"], [])[:3],
            }
        )
    out.sort(key=lambda r: -r["vol_increase_pct"])
    return out[: config.TOP_MOVERS]


# ---------------------------------------------------------------- 헤드라인
def _fmt_eok(v: float) -> str:
    sign = "+" if v > 0 else "−" if v < 0 else ""
    a = abs(v)
    if a >= 10000:
        return f"{sign}{a / 10000:.2f}조"
    return f"{sign}{a:,.0f}억"


def build_headline(
    market: dict, investors: dict, themes: list[dict], rotation: dict
) -> list[str]:
    lines: list[str] = []

    kospi = next((i for i in market["indices"] if i["name"] == "코스피"), None)
    if kospi:
        arrow = "▲" if kospi["chg_pct"] > 0 else "▼" if kospi["chg_pct"] < 0 else "―"
        lines.append(
            f"코스피 {kospi['value']:,.2f} {arrow}{abs(kospi['chg_pct']):.2f}% "
            f"(상승 {kospi['up']} / 하락 {kospi['down']})"
        )

    retail = (
        investors["retail_eok"]
        if investors["retail_eok"] is not None
        else investors["retail_proxy_eok"]
    )
    retail_tag = "" if investors["retail_eok"] is not None else "(추정)"
    lines.append(
        f"외국인 {_fmt_eok(investors['foreign_eok'])} · "
        f"기관 {_fmt_eok(investors['institution_eok'])} · "
        f"개인{retail_tag} {_fmt_eok(retail or 0)}"
    )

    if themes:
        buy = themes[0]
        sell = themes[-1]
        lines.append(
            f"수급 1위 테마 {buy['name']} {_fmt_eok(buy['net_eok'])}, "
            f"최하위 {sell['name']} {_fmt_eok(sell['net_eok'])}"
        )

    if rotation.get("pairs"):
        p = rotation["pairs"][0]
        lines.append(
            f"자금 이동(추정): {p['from']} → {p['to']} 약 {_fmt_eok(p['amount_eok'])}"
        )

    return lines


# ---------------------------------------------------------------- 엔트리
def analyze(
    snapshot: dict,
    themes: dict[str, list[str]],
    stock_themes: dict[str, list[str]],
    *,
    previous_themes: list[dict] | None = None,
    history_themes: list[list[dict]] | None = None,
    theme_source: str = "",
) -> dict[str, Any]:
    investors = summarize_investors(snapshot)
    theme_rows = rollup_themes(snapshot, themes)
    rotation = compute_rotation(theme_rows, previous_themes)
    streaks = compute_streaks(theme_rows, history_themes or [])
    for t in theme_rows:
        t["streak"] = streaks.get(t["name"], 0)

    # 대형주 하나가 끌고 가는 테마는 순위에서 뺀다. 전부 걸러지는 날에는
    # 화면이 비는 것보다 낫기에 전체 목록으로 되돌린다.
    featured = [t for t in theme_rows if t["featured"]] or theme_rows

    market = snapshot["market"]
    spikes = find_spikes(snapshot, stock_themes)

    movers = sorted(snapshot["flows"], key=lambda r: -r["net"])
    top_buy = [
        {
            "code": m["code"],
            "name": m["name"],
            "net_eok": to_eok(m["net"]),
            "foreign_eok": to_eok(m["frgn"]),
            "institution_eok": to_eok(m["orgn"]),
            "chg_pct": m["chg_pct"],
            "themes": stock_themes.get(m["code"], [])[:2],
        }
        for m in movers[: config.TOP_MOVERS]
    ]
    top_sell = [
        {
            "code": m["code"],
            "name": m["name"],
            "net_eok": to_eok(m["net"]),
            "foreign_eok": to_eok(m["frgn"]),
            "institution_eok": to_eok(m["orgn"]),
            "chg_pct": m["chg_pct"],
            "themes": stock_themes.get(m["code"], [])[:2],
        }
        for m in movers[-config.TOP_MOVERS :][::-1]
    ]

    sectors = sorted(market.get("sectors", []), key=lambda s: -s["chg_pct"])

    return {
        "date": snapshot["date"],
        "stage": snapshot["stage"],
        "collected_at": snapshot["collected_at"],
        "theme_source": theme_source,
        "amount_unit_detected": snapshot.get("amount_unit_detected", ""),
        "headline": build_headline(market, investors, featured, rotation),
        "indices": market["indices"],
        "investors": investors,
        "themes": theme_rows,
        # 부호로 자른다. 개수로만 자르면 순매수인 테마가
        # '자금이 빠져나간 테마' 칸에 섞여 들어간다.
        "themes_top": [t for t in featured if t["net_eok"] > 0][: config.TOP_THEMES],
        "themes_bottom": [t for t in featured if t["net_eok"] < 0][
            -config.TOP_THEMES :
        ][::-1],
        # 대표성 기준에 걸려 상단에서 빠진 테마 수 (화면에 사유를 적는다)
        "themes_demoted": len(theme_rows) - len(featured),
        "rotation": rotation,
        "sectors": sectors,
        "spikes": spikes,
        "top_buy": top_buy,
        "top_sell": top_sell,
    }
