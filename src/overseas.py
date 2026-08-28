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
from datetime import datetime, timedelta

import requests

import config
from src.kis import KisClient

log = logging.getLogger(__name__)

CODE_CACHE = config.CACHE_DIR / "us_codes.json"

# div: N=해외지수 X=환율 I=국채
# 앞의 코드가 실제로 통하는 표기다 (2026-08-27 probe 로 확인). 뒤는 예비.
# 다우만 점이 붙는다. 'DJI' 는 0행, '.DJI' 가 나온다.
INDICES = [
    {"key": "spx", "name": "S&P 500", "div": "N", "codes": ["SPX", ".SPX"]},
    {"key": "comp", "name": "나스닥", "div": "N", "codes": ["COMP", ".IXIC"]},
    {"key": "dji", "name": "다우", "div": "N", "codes": [".DJI", "DJI", "INDU"]},
]

MACRO = [
    {"key": "vix", "name": "VIX", "div": "N", "codes": ["VIX", ".VIX"], "unit": "pt"},
    {
        "key": "usdkrw", "name": "원/달러", "div": "X",
        "codes": ["FX@KRW", "KRW"], "unit": "won",
    },
]

# 미 10년물은 KIS 국채 구분(I)에 TNX / .TNX / US10YT / TNX@US 어느 표기로도 안 잡힌다
# (probe 에서 전부 0행). 야후 시세로 받는다.
UST10 = {"key": "ust10", "name": "미 10년물", "unit": "yield", "yahoo": "^TNX"}

# SPDR 섹터 11종 + 반도체(SMH). 반도체는 섹터가 아니라 업종 ETF 지만,
# 코스피 수급이 가장 크게 따라 움직이는 자리라 같이 놓는다.
SECTORS = [
    ("XLK", "기술"), ("XLC", "커뮤니케이션"), ("XLY", "경기소비재"),
    ("XLP", "필수소비재"), ("XLE", "에너지"), ("XLF", "금융"),
    ("XLV", "헬스케어"), ("XLI", "산업재"), ("XLB", "소재"),
    ("XLRE", "부동산"), ("XLU", "유틸리티"), ("SMH", "반도체"),
]

# SPDR 11종은 AMS 에서 나오는데 SMH 만 나스닥이다 (probe 확인). 헛걸음 두 번을 줄인다.
SECTOR_ON_NAS = {"SMH"}

# 국내 투자자가 많이 보는 미국 종목. 시가총액 상위와 반도체·AI 축을 함께 담는다.
# 코스피 반도체 수급이 간밤 이쪽을 따라가는 일이 잦아 그 자리를 두껍게 뒀다.
WATCH = [
    ("NVDA", "엔비디아"), ("MSFT", "마이크로소프트"), ("AAPL", "애플"),
    ("GOOGL", "알파벳"), ("AMZN", "아마존"), ("META", "메타"),
    ("AVGO", "브로드컴"), ("TSLA", "테슬라"), ("TSM", "TSMC"),
    ("AMD", "AMD"), ("MU", "마이크론"), ("ASML", "ASML"),
    ("ARM", "ARM"), ("QCOM", "퀄컴"), ("INTC", "인텔"),
    ("NFLX", "넷플릭스"), ("PLTR", "팔란티어"), ("COIN", "코인베이스"),
    ("LLY", "일라이릴리"), ("JPM", "JP모건"),
]

# 체크리스트 지표. KIS 가 어느 시장 구분(N/X/I/S)에 무슨 표기로 넣어 두는지 자료가
# 엇갈려서, (구분, 코드) 쌍을 순서대로 넣어 본다. 끝내 안 나오면 대표 ETF 로 대신
# 보여주되 화면에 '대용' 이라고 밝힌다. 유가 대신 유가 ETF 를 슬쩍 보여주고 원유
# 가격이라고 부르지는 않는다.
EXTRAS = [
    {
        "key": "wti", "name": "국제유가 (WTI)", "unit": "usd",
        "pairs": [("S", "CL"), ("S", "WTI"), ("N", "WTI")],
        "yahoo": "CL=F",
    },
    {
        "key": "dxy", "name": "달러지수", "unit": "pt",
        "pairs": [("X", "DXY"), ("X", ".DXY"), ("N", "DXY")],
        "yahoo": "DX-Y.NYB",
    },
]

# NYSE Arca ETF 는 KIS 에서 보통 AMS 로 잡힌다. 아니면 다음 순서로 찾아본다.
EXCHANGES = ["AMS", "NYS", "NAS"]
# 개별 종목은 반대로 나스닥이 먼저다. 헛걸음 한 번을 줄인다.
STOCK_EXCHANGES = ["NAS", "NYS", "AMS"]


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


def last_closed_us_date(now: datetime | None = None) -> str:
    """마감이 끝난 가장 최근 현지 날짜(YYYYMMDD). 휴장일은 따지지 않는다.

    한국은 KST, 미국은 현지 시각이라 날짜가 어긋난다. KST 8/28 낮에 '오늘'을
    물으면 현지는 아직 8/28 새벽이고 장이 열리지도 않았다. 그 시점의 최신
    마감일은 8/27 이다.
    """
    now = (now or datetime.now(config.KST)).astimezone(config.US_TZ)
    if (now.hour, now.minute) >= config.US_CLOSE:
        return now.strftime("%Y%m%d")          # 오늘 장이 이미 끝났다
    return (now - timedelta(days=1)).strftime("%Y%m%d")


def _closed_only(
    rows: list[tuple[str, float]], now: datetime | None = None
) -> list[tuple[str, float]]:
    """아직 안 끝난 세션의 행을 버린다.

    KIS 해외주식 일봉(HHDFS76240000)은 장이 열리기도 전에 그날 날짜로 행을 준다.
    프리마켓 값이라 그걸 종가로 쓰면 '간밤 마감' 이 아니라 '지금 시간외' 가 된다.
    실제로 엔비디아가 8/27 에 +8.74% 로 끝났는데 화면에는 -1.06% 로 나왔다.
    """
    cutoff = last_closed_us_date(now)
    return [r for r in rows if r[0] <= cutoff]


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
    return _closed_only(out)


def _chart(rows: list[tuple[str, float]], n: int = 30) -> list[list]:
    """체크리스트 그래프용. 오래된 것부터 [["2026-08-27", 82.38], …] 로 최대 n개."""
    return [[f"{d[:4]}-{d[4:6]}-{d[6:]}", round(v, 4)] for d, v in rows[:n]][::-1]


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
        # 금리는 0.01%p 단위로 움직인다. 여기서 두 자리로 깎으면 변화가 0 이 된다.
        "change": round(last - prev, 4),
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


def _fetch_stock(
    kis: KisClient, symbol: str, codes: dict, prefer: list[str] | None = None
) -> list[tuple[str, float]]:
    """거래소 코드도 같은 방식으로 찾는다."""
    key = f"excd:{symbol}"
    known = codes.get(key)
    order = list(prefer or EXCHANGES)
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


def _fetch_pairs(
    kis: KisClient, key: str, pairs: list[tuple[str, str]], codes: dict
) -> list[tuple[str, float]]:
    """(시장구분, 코드) 쌍을 순서대로 넣어 보고 값이 오는 첫 조합을 쓴다."""
    known = codes.get(key)
    order = list(pairs)
    if known:
        prev = tuple(known.split(":", 1))
        order = [prev] + [x for x in order if x != prev]

    for div, code in order:
        try:
            rows = _series(kis.overseas_index_daily(code, market_div=div))
        except Exception as exc:
            log.debug("%s (%s/%s) 실패: %s", key, div, code, exc)
            continue
        if len(rows) >= 2:
            tag = f"{div}:{code}"
            if codes.get(key) != tag:
                log.info("%s 코드 확정: %s", key, tag)
                codes[key] = tag
            return rows
    return []


def _fetch_yahoo(symbol: str) -> list[tuple[str, float]]:
    """KIS 가 안 주는 것만 야후에서 받는다 (미 10년물·WTI·달러지수).

    키가 필요 없고 일봉 종가만 쓰므로 앞의 KIS 경로와 같은 모양으로 맞춰 돌려준다.
    """
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"range": "3mo", "interval": "1d"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        r.raise_for_status()
        res = r.json()["chart"]["result"][0]
        stamps = res["timestamp"]
        closes = res["indicators"]["quote"][0]["close"]
    except Exception as exc:
        log.warning("야후 시세 실패 (%s): %s", symbol, exc)
        return []

    from datetime import datetime, timezone

    rows = []
    for ts, close in zip(stamps, closes):
        if close is None:
            continue
        date = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y%m%d")
        rows.append((date, float(close)))
    rows.sort(key=lambda x: x[0], reverse=True)
    return _closed_only(rows)


def _fetch_btc() -> dict | None:
    """비트코인. KIS 에는 없어서 공개 시세를 쓴다.

    업비트 일봉(원화)을 먼저 본다. 국내에서 보는 값이 그쪽이고 키도 필요 없다.
    다른 항목과 같은 방식(종가 두 개로 등락 계산 + 그래프용 시계열)을 쓰려고
    현재가가 아니라 일봉으로 받는다. 막히면 야후 BTC-USD 로 넘어간다.
    """
    try:
        r = requests.get(
            "https://api.upbit.com/v1/candles/days",
            params={"market": "KRW-BTC", "count": 30},
            headers={"Accept": "application/json"},
            timeout=10,
        )
        r.raise_for_status()
        rows = [
            (c["candle_date_time_kst"][:10].replace("-", ""), float(c["trade_price"]))
            for c in r.json()
        ]
        m = _move(rows)
        if m:
            return {
                "key": "btc", "name": "비트코인",
                "value": round(m["value"]), "change": round(m["change"]),
                "chg_pct": m["chg_pct"], "unit": "krw",
                "note": "업비트 · 전일 대비", "series": _chart(rows),
            }
    except Exception as exc:
        log.warning("업비트 시세 실패: %s", exc)

    rows = _fetch_yahoo("BTC-USD")
    m = _move(rows)
    if m:
        return {
            "key": "btc", "name": "비트코인",
            "value": m["value"], "change": m["change"], "chg_pct": m["chg_pct"],
            "unit": "usd", "note": "야후 시세", "series": _chart(rows),
        }

    return None


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
    out: dict = {
        "as_of": "", "indices": [], "macro": [],
        "sectors": [], "stocks": [], "extras": [],
    }
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
        out["macro"].append(
            {
                "name": spec["name"],
                "value": value,
                "change": change,
                "chg_pct": m["chg_pct"],
                "unit": spec["unit"],
            }
        )

    ust10_rows = _fetch_yahoo(UST10["yahoo"])
    m = _move(ust10_rows)
    if m:
        y = _yield_value(m["value"])
        if y is None:
            log.warning("미 10년물 값이 금리로 보이지 않습니다 (%s). 뺍니다.", m["value"])
        else:
            scale = y / m["value"] if m["value"] else 1
            out["macro"].append(
                {
                    "name": UST10["name"], "value": round(y, 2),
                    "change": round(m["change"] * scale, 3),   # %p
                    "chg_pct": m["chg_pct"], "unit": "yield", "note": "야후 시세",
                    "series": [[d, round(v * scale, 4)] for d, v in _chart(ust10_rows)],
                }
            )

    for symbol, name in SECTORS:
        order = STOCK_EXCHANGES if symbol in SECTOR_ON_NAS else EXCHANGES
        m = _move(_fetch_stock(kis, symbol, codes, order))
        if not m:
            continue
        dates.append(m["date"])
        out["sectors"].append(
            {"symbol": symbol, "name": name, "value": m["value"], "chg_pct": m["chg_pct"]}
        )

    for symbol, name in WATCH:
        m = _move(_fetch_stock(kis, symbol, codes, STOCK_EXCHANGES))
        if not m:
            continue
        dates.append(m["date"])
        out["stocks"].append(
            {"symbol": symbol, "name": name, "value": m["value"], "chg_pct": m["chg_pct"]}
        )

    for spec in EXTRAS:
        rows = _fetch_pairs(kis, spec["key"], spec["pairs"], codes)
        note = ""
        if not rows and spec.get("yahoo"):
            rows = _fetch_yahoo(spec["yahoo"])
            note = "야후 시세"
        m = _move(rows)
        if not m:
            log.warning("%s: 값을 못 가져왔습니다", spec["name"])
            continue
        dates.append(m["date"])
        out["extras"].append(
            {
                "key": spec["key"], "name": spec["name"], "unit": spec["unit"],
                "value": m["value"], "change": m["change"], "chg_pct": m["chg_pct"],
                "note": note, "series": _chart(rows),
            }
        )

    btc = _fetch_btc()
    if btc:
        out["extras"].append(btc)

    _save_codes(codes)

    if not out["indices"] and not out["sectors"]:
        log.warning("미국증시 수집 결과가 비었습니다.")
        return {}

    # 현지 마감일. 항목마다 하루씩 어긋날 수 있어 가장 흔한 날짜를 쓴다.
    out["as_of"] = max(set(dates), key=dates.count) if dates else ""
    out["sectors"].sort(key=lambda s: s["chg_pct"], reverse=True)
    out["stocks"].sort(key=lambda s: s["chg_pct"], reverse=True)
    log.info(
        "미국증시 수집: 지수 %d · 매크로 %d · 섹터 %d · 종목 %d · 체크리스트 %d (현지 %s)",
        len(out["indices"]), len(out["macro"]), len(out["sectors"]),
        len(out["stocks"]), len(out["extras"]), out["as_of"],
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

    for spec in EXTRAS:
        for div, code in spec["pairs"]:
            try:
                rows = _series(kis.overseas_index_daily(code, market_div=div))
                note = f"{len(rows):>4}  {rows[0][0] if rows else '—':<10} {rows[0][1] if rows else '':>10}"
            except Exception as exc:
                note = f"{'—':>4}  실패: {str(exc)[:38]}"
            print(f"{spec['name']:<14} {div:<4} {code:<10} {note}")

    for label, sym in (("미 10년물", UST10["yahoo"]),) + tuple(
        (e["name"], e["yahoo"]) for e in EXTRAS if e.get("yahoo")
    ):
        rows = _fetch_yahoo(sym)
        note = f"{len(rows):>4}  {rows[0][0] if rows else '—':<10} {rows[0][1] if rows else '':>10}"
        print(f"{label:<14} {'야후':<4} {sym:<10} {note}")

    btc = _fetch_btc()
    print(f"{'비트코인':<14} {'—':<4} {'—':<10} {btc['value'] if btc else '실패'} ({btc['note'] if btc else ''})")

    for symbol, name in SECTORS + WATCH[:3]:   # 종목은 대표 3개만 확인
        for excd in (EXCHANGES if (symbol, name) in SECTORS else STOCK_EXCHANGES):
            rows: list = []
            try:
                rows = _series(kis.overseas_stock_daily(symbol, excd))
                note = f"{len(rows):>4}  {rows[0][0] if rows else '—':<10} {rows[0][1] if rows else '':>10}"
            except Exception as exc:
                note = f"{'—':>4}  실패: {str(exc)[:38]}"
            print(f"{name + '(' + symbol + ')':<14} {excd:<4} {'':<10} {note}")
            if len(rows) >= 2:
                break   # 되는 거래소를 찾으면 다음 종목으로
