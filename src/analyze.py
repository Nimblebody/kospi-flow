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

    # 커버리지의 분모는 '조회한 종목' 이어야 한다. 테마 마스터에는 코스닥 종목도
    # 섞여 있는데 이건 애초에 조회 대상이 아니라, 분모에 넣으면 멀쩡한 테마가
    # 표본이 얇은 것처럼 보인다. 유니버스가 없으면(가집계) 예전대로 전체를 센다.
    universe = set(snapshot.get("universe") or ())

    result: list[dict] = []
    for name, codes in themes.items():
        members = [by_code[c] for c in codes if c in by_code]
        listed = [c for c in codes if c in universe] if universe else codes
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
                "members_total": len(listed),
                "members_with_data": len(active),
                # 참여 폭: 수급이 잡힌 종목 중 순매수인 비율 (한 종목이 끌고 가는지 판별)
                "breadth": round(buys / len(active), 2) if active else 0.0,
                "top1_share": top1_share,
                "top1_name": ranked[0]["name"] if ranked else "",
                # 구성종목 중 실제로 수급이 잡힌 비율 (읽을 때 표본 크기 감을 주려고)
                "coverage": round(len(active) / len(listed), 3) if listed else 0.0,
                # 상단 노출 자격. 아래 analyze() 에서 themes_top/bottom 을 고를 때 쓴다.
                "featured": (
                    top1_share < config.THEME_MAX_TOP1_SHARE
                    and len(active) >= config.THEME_MIN_DATA_MEMBERS
                ),
                "avg_chg_pct": round(sum(chgs) / len(chgs), 2) if chgs else 0.0,
                # 묶기 판정용. merge_contained_themes() 가 쓰고 나서 지운다.
                "_codes": frozenset(m["code"] for m in active),
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


# ---------------------------------------------------------------- 겹치는 테마 묶기
def merge_contained_themes(rows: list[dict]) -> list[dict]:
    """구성종목이 다른 테마에 완전히 포함되면 그 테마 아래로 흡수한다.

    수급 API 가 상위 30종목만 주는 탓에, 웹툰·빅데이터처럼 성격이 다른 테마도
    데이터가 잡힌 종목만 보면 똑같아진다. 겹침 비율에 임계값을 두면 몇 %로
    할지가 자의적이라, '전부 포함될 때' 라는 사실 관계로만 묶는다.

    대표는 featured 를 먼저 고른다. 순서를 금액만으로 잡으면 대표성 없는 테마가
    호스트가 되어, 밑에 딸린 멀쩡한 테마까지 화면에서 통째로 사라진다.

    rows 를 제자리에서 수정한다 (각 행에 "merged" 를 넣고 "_codes" 를 지운다).
    돌려주는 값은 흡수되지 않고 남은 대표 테마들이다.
    """
    hosts: list[dict] = []
    for t in sorted(rows, key=lambda r: (not r["featured"], -abs(r["net_eok"]))):
        t["merged"] = []
        codes = t["_codes"]
        if not codes:
            continue
        host = next((h for h in hosts if codes <= h["_codes"]), None)
        if host:
            host["merged"].append(t["name"])
        else:
            hosts.append(t)

    for t in rows:
        t.pop("_codes", None)

    # 들어온 순서(금액 내림차순)를 유지해서 돌려준다
    kept = {h["name"] for h in hosts}
    return [t for t in rows if t["name"] in kept]


# ---------------------------------------------------------------- 자금 이동
def compute_rotation(
    today: list[dict], previous: list[dict] | None, *, top_n: int | None = None
) -> dict:
    """전 거래일 대비 테마별 순매수 증감으로 자금 이동을 추정한다."""
    top_n = config.ROTATION_TOP_THEMES if top_n is None else top_n
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
        "pairs": pairs[: config.ROTATION_MAX_PAIRS],
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


# ---------------------------------------------------------------- 업종 구성종목
def attach_sector_stocks(
    sectors: list[dict], snapshot: dict, stock_sectors: dict[str, str]
) -> list[dict]:
    """업종별로 수급이 잡힌 종목을 순매수 크기순으로 붙인다.

    업종시세 API 는 지수만 주고 구성종목을 주지 않는다. 종목-업종 매핑은
    코스피 종목 마스터에서 가져온다 (masters.load_stock_sectors).

    '종합' '대형주' '고배당50' 'VKOSPI' 처럼 업종이 아닌 항목에는 아무것도
    안 붙는다. 원래 구성종목이라는 게 없는 지수·규모구분이다.
    """
    by_sector: dict[str, list[dict]] = {}
    for r in snapshot["flows"]:
        code = stock_sectors.get(r["code"])
        if code:
            by_sector.setdefault(code, []).append(r)

    out = []
    for s in sectors:
        members = by_sector.get(s.get("code") or "", [])
        members.sort(key=lambda m: -abs(m["net"]))
        out.append(
            {
                **s,
                "members_with_data": len(members),
                "stocks": [
                    {
                        "code": m["code"],
                        "name": m["name"],
                        "net_eok": to_eok(m["net"]),
                        "foreign_eok": to_eok(m["frgn"]),
                        "institution_eok": to_eok(m["orgn"]),
                        "chg_pct": m["chg_pct"],
                    }
                    for m in members[: config.SECTOR_STOCKS]
                ],
            }
        )
    return out


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
    stock_sectors: dict[str, str] | None = None,
) -> dict[str, Any]:
    investors = summarize_investors(snapshot)
    theme_rows = rollup_themes(snapshot, themes)
    rotation = compute_rotation(theme_rows, previous_themes)
    streaks = compute_streaks(theme_rows, history_themes or [])
    for t in theme_rows:
        t["streak"] = streaks.get(t["name"], 0)

    # 구성종목이 다른 테마에 완전히 포함되는 테마부터 흡수하고,
    # 남은 대표 중에서 대형주 하나가 끌고 가는 테마를 뺀다.
    # 전부 걸러지는 날에는 화면이 비는 것보다 낫기에 되돌린다.
    hosts = merge_contained_themes(theme_rows)
    featured = [t for t in hosts if t["featured"]] or hosts

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
    if stock_sectors:
        sectors = attach_sector_stocks(sectors, snapshot, stock_sectors)

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
        # 겹쳐서 묶인 테마 수 / 대표성 기준에 걸려 빠진 테마 수 (화면에 사유를 적는다)
        "themes_merged": len(theme_rows) - len(hosts),
        "themes_demoted": len(hosts) - len(featured),
        "rotation": rotation,
        "sectors": sectors,
        "spikes": spikes,
        "top_buy": top_buy,
        "top_sell": top_sell,
    }
