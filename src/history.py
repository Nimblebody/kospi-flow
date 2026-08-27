# 일별 확정 투자자매매동향(FHPTJ04160001)으로 과거 수급 시계열을 수집한다
"""
가집계(FHPTJ04400000)와 확정치(FHPTJ04160001)의 차이

  가집계는 상위 30종목의 '순위' 만 주고 날짜를 못 고른다. 장중 어림치라
  확정치와 값이 꽤 다르다 (삼성전자 2026-08-27 기준 외국인 +1,506억 vs +3,700억,
  기관은 부호까지 반대였다).

  확정치는 종목당 1콜이지만 한 번에 30일치를 돌려준다. 그래서 2주 백필 비용이
  하루치 비용과 같다. 커버리지도 상위 30종목이 아니라 조회한 종목 전부다.

자금 이동(rotation)은 어제 대비 증감이라 두 날이 같은 출처여야 한다.
섞으면 출처 차이가 자금 이동으로 둔갑한다.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import config
from src import masters
from src.kis import KisClient

log = logging.getLogger(__name__)

# 순매수 대금 필드(*_ntby_tr_pbmn)의 단위. 수량×종가로 교차검증했다.
PBMN_UNIT = 1e6  # 백만원


def _num(v, default: float = 0.0) -> float:
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def universe() -> list[str]:
    """테마에 속하면서 코스피에 상장된 종목. 테마 롤업에 쓰이는 종목이 전부 여기 있다."""
    themes, _ = masters.load_themes()
    codes: set[str] = set()
    for members in themes.values():
        codes.update(members)
    kospi = set(masters.load_stock_names())
    return sorted(codes & kospi)


def business_days(end: str, count: int) -> list[str]:
    """end(YYYYMMDD) 포함 이전 영업일(주말 제외)을 오래된 순으로 count 개."""
    d = datetime.strptime(end, "%Y%m%d")
    out: list[str] = []
    while len(out) < count:
        if d.weekday() < 5:
            out.append(d.strftime("%Y%m%d"))
        d -= timedelta(days=1)
    return sorted(out)


def collect_series(
    kis: KisClient, codes: list[str], end_date: str, dates: set[str]
) -> dict[str, dict[str, dict]]:
    """{날짜: {종목코드: 수급레코드}} 를 만든다.

    종목당 1콜이고 응답에 30일치가 들어 있어, dates 가 그 안이면 추가 콜이 없다.
    콜 하나가 왕복 0.9초쯤 걸려 순차로 돌면 752종목에 11분이다. 동시에 띄우고
    초당 건수는 KisClient 의 레이트 리미터에 맡긴다.
    """
    names = masters.load_stock_names()
    by_date: dict[str, dict[str, dict]] = {d: {} for d in dates}

    def fetch(code: str) -> tuple[str, list | None]:
        try:
            return code, kis.investor_trade_by_stock_daily(code, end_date)
        except Exception as exc:
            log.debug("일별 수급 조회 실패 %s: %s", code, exc)
            return code, None

    kis.token  # 토큰 발급 경쟁을 피해 스레드 시작 전에 한 번 받아둔다
    failed = done = 0
    with ThreadPoolExecutor(max_workers=config.KIS_RATE_LIMIT_PER_SEC) as pool:
        results = pool.map(fetch, codes)

        # pool.map 은 순서대로 하나씩 돌려주므로 여기는 단일 스레드다
        for code, rows in results:
            done += 1
            if done % 100 == 0:
                log.info("일별 수급 %d/%d 종목", done, len(codes))
            if rows is None:
                failed += 1
                continue

            for r in rows:
                date = (r.get("stck_bsop_date") or "").strip()
                if date not in by_date:
                    continue
                frgn = _num(r.get("frgn_ntby_tr_pbmn")) * PBMN_UNIT
                orgn = _num(r.get("orgn_ntby_tr_pbmn")) * PBMN_UNIT
                prsn = _num(r.get("prsn_ntby_tr_pbmn")) * PBMN_UNIT
                if frgn == 0 and orgn == 0 and prsn == 0:
                    continue  # 거래가 없던 날은 담지 않는다
                by_date[date][code] = {
                    "code": code,
                    "name": names.get(code, code),
                    "price": _num(r.get("stck_clpr")),
                    "chg_pct": _num(r.get("prdy_ctrt")),
                    "volume": _num(r.get("acml_vol")),
                    "frgn": frgn,
                    "orgn": orgn,
                    "prsn": prsn,
                    "net": frgn + orgn,
                }

    if failed:
        log.warning("일별 수급 조회 실패 %d종목 (건너뜀)", failed)
    for d in sorted(by_date):
        log.info("  %s: %d종목", d, len(by_date[d]))
    return by_date


def make_snapshot(
    date: str,
    flows: dict[str, dict],
    market: dict | None = None,
    codes: list[str] | None = None,
) -> dict:
    """analyze() 가 그대로 먹는 스냅샷 형태로 감싼다.

    과거 날짜는 지수·업종 시세를 되돌려주는 API 가 없어 market 이 비어 있다.
    build_headline 은 코스피 지수가 없으면 그 줄을 건너뛴다.

    codes 는 실제로 조회한 종목(유니버스). 테마 커버리지의 분모로 쓴다.
    이게 없으면 조회조차 안 한 코스닥 종목까지 분모에 들어가, 멀쩡한 테마가
    '표본이 얇다' 고 잘못 표시된다.
    """
    return {
        "date": date,
        "stage": "final",
        "collected_at": datetime.now(config.KST).isoformat(timespec="seconds"),
        "amount_unit_detected": "백만원(확정)",
        "flows": list(flows.values()),
        "market": market or {"indices": [], "sectors": []},
        "volume_leaders": [],
        "universe": list(codes) if codes else [],
    }
