"""
KIS API 에서 하루치 원본 스냅샷을 수집한다.

수집 결과는 analyze.py 가 그대로 먹을 수 있는 순수 dict 이고,
JSON 으로 떨어뜨려 두므로 나중에 재분석·백테스트도 된다.
"""
from __future__ import annotations

import logging
from datetime import datetime

import config
from src.kis import KisClient

log = logging.getLogger(__name__)


def _num(v, default: float = 0.0) -> float:
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def _detect_amount_multiplier(max_raw: float) -> tuple[float, str]:
    """순매수 대금 필드의 단위를 값의 크기로 추정한다.

    코스피에서 하루 종목별 외국인/기관 순매수 1위는 보통 수백억~수천억원
    (1e10 ~ 1e12 원) 수준이다. 그 범위에 들어오는 배수를 고른다.
    KIS 가 문서상 단위를 바꾸더라도 자동으로 따라간다.
    """
    if max_raw <= 0:
        return 1.0, "원(판단불가)"
    for mult, label in ((1.0, "원"), (1e3, "천원"), (1e6, "백만원"), (1e8, "억원")):
        if 1e9 <= max_raw * mult <= 5e12:
            return mult, label
    return 1e6, "백만원(기본값)"


# ---------------------------------------------------------------- 수급
_FIT_QUERIES = [
    ("0", "0"),  # 전체 순매수 상위
    ("0", "1"),  # 전체 순매도 상위
    ("1", "0"),  # 외국인 순매수 상위
    ("1", "1"),  # 외국인 순매도 상위
    ("2", "0"),  # 기관계 순매수 상위
    ("2", "1"),  # 기관계 순매도 상위
]


def collect_flows(kis: KisClient) -> tuple[dict[str, dict], str]:
    """종목별 외국인·기관 순매수 대금을 모은다.

    가집계 TR 은 '순위' 를 주므로 상·하위 양쪽을 모두 긁어
    자금이 크게 들어온 종목과 크게 빠진 종목을 전부 확보한다.
    """
    rows: dict[str, dict] = {}
    for who, sort in _FIT_QUERIES:
        try:
            out = kis.foreign_institution_total(who=who, sort=sort, by_amount=True)
        except Exception as exc:
            log.warning("가집계 조회 실패 (who=%s sort=%s): %s", who, sort, exc)
            continue
        for r in out:
            code = (r.get("mksc_shrn_iscd") or "").strip()
            if len(code) != 6:
                continue
            rec = rows.setdefault(
                code,
                {
                    "code": code,
                    "name": (r.get("hts_kor_isnm") or "").strip(),
                    "price": _num(r.get("stck_prpr")),
                    "chg_pct": _num(r.get("prdy_ctrt")),
                    "volume": _num(r.get("acml_vol")),
                    "frgn_raw": 0.0,
                    "orgn_raw": 0.0,
                },
            )
            # 같은 종목이 여러 순위에 걸쳐 나오면 절대값이 큰 쪽을 채택
            frgn = _num(r.get("frgn_ntby_tr_pbmn"))
            orgn = _num(r.get("orgn_ntby_tr_pbmn"))
            if abs(frgn) > abs(rec["frgn_raw"]):
                rec["frgn_raw"] = frgn
            if abs(orgn) > abs(rec["orgn_raw"]):
                rec["orgn_raw"] = orgn
            if not rec["name"]:
                rec["name"] = (r.get("hts_kor_isnm") or "").strip()

    max_raw = max(
        (max(abs(r["frgn_raw"]), abs(r["orgn_raw"])) for r in rows.values()),
        default=0.0,
    )
    mult, unit_label = _detect_amount_multiplier(max_raw)
    log.info(
        "수급 %d종목 수집, 대금 단위 추정 = %s (최대 원시값 %.0f)",
        len(rows), unit_label, max_raw,
    )
    for r in rows.values():
        r["frgn"] = r.pop("frgn_raw") * mult  # 원
        r["orgn"] = r.pop("orgn_raw") * mult  # 원
        r["net"] = r["frgn"] + r["orgn"]
    return rows, unit_label


# ---------------------------------------------------------------- 시장 전경
def collect_market(kis: KisClient) -> dict:
    out: dict = {"indices": [], "sectors": []}

    for iscd, label in (("0001", "코스피"), ("1001", "코스닥")):
        try:
            d = kis.index_price(iscd)
        except Exception as exc:
            log.warning("지수 조회 실패 %s: %s", label, exc)
            continue
        out["indices"].append(
            {
                "name": label,
                "value": _num(d.get("bstp_nmix_prpr")),
                "change": _num(d.get("bstp_nmix_prdy_vrss")),
                "chg_pct": _num(d.get("bstp_nmix_prdy_ctrt")),
                "volume": _num(d.get("acml_vol")),
                "amount": _num(d.get("acml_tr_pbmn")),
                "up": int(_num(d.get("ascn_issu_cnt"))),
                "down": int(_num(d.get("down_issu_cnt"))),
                "flat": int(_num(d.get("stnr_issu_cnt"))),
            }
        )

    try:
        for s in kis.index_category_price("K"):
            name = (s.get("hts_kor_isnm") or "").strip()
            if not name:
                continue
            out["sectors"].append(
                {
                    "name": name,
                    # 종목-업종 매핑(masters.load_stock_sectors)과 같은 체계의 코드
                    "code": (s.get("bstp_cls_code") or "").strip(),
                    "chg_pct": _num(s.get("bstp_nmix_prdy_ctrt")),
                    "amount": _num(s.get("acml_tr_pbmn")),
                    "amount_share": _num(s.get("acml_tr_pbmn_rlim")),
                }
            )
    except Exception as exc:
        log.warning("업종별 시세 조회 실패: %s", exc)

    return out


def collect_volume_leaders(kis: KisClient) -> list[dict]:
    """거래대금 상위 + 거래증가율 상위를 합쳐서 '오늘 돈이 몰린 종목'."""
    leaders: dict[str, dict] = {}
    for belong, tag in (("3", "거래대금"), ("1", "거래증가율")):
        try:
            rows = kis.volume_rank(belong=belong)
        except Exception as exc:
            log.warning("거래량순위 조회 실패 (%s): %s", tag, exc)
            continue
        for r in rows:
            code = (r.get("mksc_shrn_iscd") or "").strip()
            if len(code) != 6:
                continue
            rec = leaders.setdefault(
                code,
                {
                    "code": code,
                    "name": (r.get("hts_kor_isnm") or "").strip(),
                    "chg_pct": _num(r.get("prdy_ctrt")),
                    "amount": _num(r.get("acml_tr_pbmn")),
                    "volume": _num(r.get("acml_vol")),
                    "vol_increase_pct": _num(r.get("vol_inrt")),
                    "tags": [],
                },
            )
            if tag not in rec["tags"]:
                rec["tags"].append(tag)
            if not rec["vol_increase_pct"]:
                rec["vol_increase_pct"] = _num(r.get("vol_inrt"))
    return list(leaders.values())


# ---------------------------------------------------------------- 전체
def collect_all(kis: KisClient, *, stage: str, date: str) -> dict:
    """stage: 'flash'(장 마감 직후 속보) | 'final'(수급 확정 후)

    flash 는 가집계 순위(상위 30종목)로 빠르게 훑고,
    final 은 종목별 확정치를 직접 받는다. 자금 이동은 어제와 오늘이 같은
    출처여야 의미가 있어서, 저장되는 리포트는 final 기준으로 맞춘다.
    """
    log.info("수집 시작 stage=%s date=%s", stage, date)
    market = collect_market(kis)
    leaders = collect_volume_leaders(kis)

    if stage == "final":
        from src import history

        codes = history.universe()
        log.info("확정 수급 수집 %d종목 (종목당 1콜)", len(codes))
        series = history.collect_series(kis, codes, date, {date})
        flows = series[date]
        unit_label = "백만원(확정)"
        universe = codes
    else:
        flows, unit_label = collect_flows(kis)
        universe = []   # 가집계는 상위 30종목 순위라 유니버스 개념이 없다

    return {
        "date": date,
        "stage": stage,
        "collected_at": datetime.now(config.KST).isoformat(timespec="seconds"),
        "amount_unit_detected": unit_label,
        "flows": list(flows.values()),
        "market": market,
        "volume_leaders": leaders,
        "universe": universe,
    }
