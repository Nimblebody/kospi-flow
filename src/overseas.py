"""
미국 증시 요약 수집.

간밤(현지 기준 직전 거래일) 지수·매크로·섹터를 KIS 해외 시세 API 로 모은다.
국내 리포트가 나가는 16:10 / 18:30 KST 는 미국 장이 열리기 전이라, 여기서 말하는
'간밤' 은 그날 새벽에 끝난 현지 세션이다.

심볼 코드는 자료마다 표기가 갈린다(SPX / .SPX / .IXIC …). 오프셋을 추측하지 않고
전 구간을 훑어 확정했던 업종 매핑과 같은 방식으로, 후보를 순서대로 넣어 보고 값이
오는 첫 코드를 채택한다. 채택한 코드는 .cache/us_codes.json 에 남겨 다음 실행부터
한 번에 맞춘다. 끝내 안 되는 항목은 리포트에서 통째로 빠지고, 국내 리포트는 그대로
나간다.
"""
from __future__ import annotations

import json
import logging

import config
from src.kis import KisClient

log = logging.getLogger(__name__)

CODE_CACHE = config.CACHE_DIR / "us_codes.json"

# div: N=해외지수 X=환율 I=국채
INDICES = [
    {"key": "spx", "name": "S&P 500", "div": "N", "codes": ["SPX", ".SPX", "SPX500"]},
    {"key": "comp", "name": "나스닥", "div": "N", "codes": ["COMP", ".IXIC", "IXIC"]},
    {"key": "dji", "name": "다우", "div": "N", "codes": ["DJI", ".DJI", "INDU"]},
]

MACRO = [
    {"key": "vix", "name": "VIX", "div": "N", "codes": ["VIX", ".VIX"], "unit": "pt"},
    {
        "key": "usdkrw", "name": "원/달러", "div": "X",
        "codes": ["FX@KRW", "KRW", "USDKRW"], "unit": "won",
    },
    {
        "key": "ust10", "name": "미 10년물", "div": "I",
        "codes": ["TNX", ".TNX", "US10YT", "TNX@US"], "unit": "yield",
    },
]

# SPDR 섹터 11종 + 반도체(SMH). 반도체는 섹터가 아니라 업종 ETF 지만,
# 코스피 수급이 가장 크게 따라 움직이는 자리라 같이 놓는다.
SECTORS = [
    ("XLK", "기술"), ("XLC", "커뮤니케이션"), ("XLY", "경기소비재"),
    ("XLP", "필수소비재"), ("XLE", "에너지"), ("XLF", "금융"),
    ("XLV", "헬스케어"), ("XLI", "산업재"), ("XLB", "소재"),
    ("XLRE", "부동산"), ("XLU", "유틸리티"), ("SMH", "반도체"),
]

# NYSE Arca ETF 는 KIS 에서 보통 AMS 로 잡힌다. 아니면 다음 순서로 찾아본다.
EXCHANGES = ["AMS", "NYS", "NAS"]


# ---------------------------------------------------------------- 유틸
def _num(v, default: float = 0.0) -> float:
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def _load_codes() -> dict[str, str]:
    try:
        return json.loads(CODE_CACHE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_codes(codes: dict[str, str]) -> None:
    try:
        CODE_CACHE.write_text(
            json.dumps(codes, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


def _series(raw: list[dict]) -> list[tuple[str, float]]:
    """어느 엔드포인트에서 왔든 [(YYYYMMDD, 종가)] 최신순으로 맞춘다.

    지수/환율은 stck_bsop_date·ovrs_nmix_prpr, 해외주식은 xymd·clos 를 쓴다.
    """
    out = []
    for r in raw:
        date = (r.get("stck_bsop_date") or r.get("xymd") or "").strip()
        close = _num(r.get("ovrs_nmix_prpr") or r.get("clos"))
        if len(date) == 8 and close > 0:
            out.append((date, close))
    out.sort(key=lambda x: x[0], reverse=True)
    return out


def _move(rows: list[tuple[str, float]]) -> dict | None:
    """최신 종가와 그 전 종가로 등락을 만든다. 두 개가 없으면 못 쓴다."""
    if len(rows) < 2:
        return None
    (date, last), (_, prev) = rows[0], rows[1]
    if prev <= 0:
        return None
    return {
        "date": f"{date[:4]}-{date[4:6]}-{date[6:]}",
        "value": round(last, 2),
        "change": round(last - prev, 2),
        "chg_pct": round((last - prev) / prev * 100, 2),
    }


# ---------------------------------------------------------------- 조회
def _fetch_index(kis: KisClient, spec: dict, codes: dict) -> list[tuple[str, float]]:
    """후보 코드를 순서대로 넣어 보고 값이 오는 첫 코드를 쓴다."""
    known = codes.get(spec["key"])
    candidates = list(spec["codes"])
    if known:   # 지난번에 통한 코드부터
        candidates = [known] + [c for c in candidates if c != known]

    for code in candidates:
        try:
            rows = _series(kis.overseas_index_daily(code, market_div=spec["div"]))
        except Exception as exc:
            log.debug("%s (%s) 실패: %s", spec["name"], code, exc)
            continue
        if len(rows) >= 2:
            if codes.get(spec["key"]) != code:
                log.info("%s 코드 확정: %s", spec["name"], code)
                codes[spec["key"]] = code
            return rows
    log.warning("%s: 쓸 수 있는 코드가 없습니다 (후보 %s)", spec["name"], spec["codes"])
    return []


def _fetch_etf(kis: KisClient, symbol: str, codes: dict) -> list[tuple[str, float]]:
    """거래소 코드도 같은 방식으로 찾는다."""
    key = f"excd:{symbol}"
    known = codes.get(key)
    order = list(EXCHANGES)
    if known:
        order = [known] + [e for e in order if e != known]

    for excd in order:
        try:
            rows = _series(kis.overseas_stock_daily(symbol, excd))
        except Exception as exc:
            log.debug("%s (%s) 실패: %s", symbol, excd, exc)
            continue
        if len(rows) >= 2:
            if codes.get(key) != excd:
                log.info("%s 거래소 확정: %s", symbol, excd)
                codes[key] = excd
            return rows
    log.warning("%s: 어느 거래소에서도 안 나옵니다", symbol)
    return []


def _yield_value(v: float) -> float | None:
    """국채 금리를 % 로 맞춘다.

    ^TNX 계열은 4.28% 를 42.8 로 준다. 자릿수로 갈라 놓고, 그래도 금리로 보이지
    않는 값이면 화면에 올리지 않는다. 틀린 숫자를 그럴듯하게 보여주느니 뺀다.
    """
    if 10 <= v <= 200:
        v = v / 10
    return v if 0 < v <= 20 else None


# ---------------------------------------------------------------- 수집
def collect_us(kis: KisClient) -> dict:
    """간밤 미국 증시 한 장. 실패한 항목은 빠지고, 전부 실패하면 빈 dict."""
    codes = _load_codes()
    out: dict = {"as_of": "", "indices": [], "macro": [], "sectors": []}
    dates: list[str] = []

    for spec in INDICES:
        m = _move(_fetch_index(kis, spec, codes))
        if not m:
            continue
        dates.append(m["date"])
        out["indices"].append({"name": spec["name"], **{k: m[k] for k in ("value", "change", "chg_pct")}})

    for spec in MACRO:
        m = _move(_fetch_index(kis, spec, codes))
        if not m:
            continue
        value, change = m["value"], m["change"]
        if spec["unit"] == "yield":
            y = _yield_value(value)
            if y is None:
                log.warning("미 10년물 값이 금리로 보이지 않습니다 (%s). 뺍니다.", value)
                continue
            scale = y / value if value else 1     # 42.8 -> 4.28 이면 0.1
            change = round(change * scale, 3)     # %p
            value = round(y, 2)
        out["macro"].append(
            {
                "name": spec["name"],
                "value": value,
                "change": change,
                "chg_pct": m["chg_pct"],
                "unit": spec["unit"],
            }
        )

    for symbol, name in SECTORS:
        m = _move(_fetch_etf(kis, symbol, codes))
        if not m:
            continue
        dates.append(m["date"])
        out["sectors"].append(
            {"symbol": symbol, "name": name, "value": m["value"], "chg_pct": m["chg_pct"]}
        )

    _save_codes(codes)

    if not out["indices"] and not out["sectors"]:
        log.warning("미국증시 수집 결과가 비었습니다.")
        return {}

    # 현지 마감일. 항목마다 하루씩 어긋날 수 있어 가장 흔한 날짜를 쓴다.
    out["as_of"] = max(set(dates), key=dates.count) if dates else ""
    out["sectors"].sort(key=lambda s: s["chg_pct"], reverse=True)
    log.info(
        "미국증시 수집: 지수 %d · 매크로 %d · 섹터 %d (현지 %s)",
        len(out["indices"]), len(out["macro"]), len(out["sectors"]), out["as_of"],
    )
    return out


# ---------------------------------------------------------------- 진단
def probe(kis: KisClient) -> None:
    """후보 코드를 전부 넣어 보고 무엇이 되는지 표로 찍는다.

    KIS 해외 시세 권한 여부와 실제로 통하는 심볼 표기를 한 번에 확인하는 용도.
    python main.py --probe-us
    """
    print(f"{'항목':<14} {'구분':<4} {'코드':<10} {'행':>4}  최신일     종가")
    print("-" * 62)

    for spec in INDICES + MACRO:
        for code in spec["codes"]:
            try:
                rows = _series(kis.overseas_index_daily(code, market_div=spec["div"]))
                note = f"{len(rows):>4}  {rows[0][0] if rows else '—':<10} {rows[0][1] if rows else '':>10}"
            except Exception as exc:
                note = f"{'—':>4}  실패: {str(exc)[:38]}"
            print(f"{spec['name']:<14} {spec['div']:<4} {code:<10} {note}")

    for symbol, name in SECTORS:
        for excd in EXCHANGES:
            rows: list = []
            try:
                rows = _series(kis.overseas_stock_daily(symbol, excd))
                note = f"{len(rows):>4}  {rows[0][0] if rows else '—':<10} {rows[0][1] if rows else '':>10}"
            except Exception as exc:
                note = f"{'—':>4}  실패: {str(exc)[:38]}"
            print(f"{name + '(' + symbol + ')':<14} {excd:<4} {'':<10} {note}")
            if len(rows) >= 2:
                break   # 되는 거래소를 찾으면 다음 종목으로
