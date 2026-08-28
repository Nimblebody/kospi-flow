"""
샘플(가짜) 스냅샷 생성기 — 화면과 분석 로직 확인용.

실제 시세가 아니다. KIS 앱키를 넣기 전에 대시보드가 어떻게 보이는지,
테마 롤업과 자금이동 계산이 맞게 도는지 확인하는 용도.
"""
from __future__ import annotations

import random
from datetime import datetime

import config

_NAMES = {
    "005930": "삼성전자", "000660": "SK하이닉스", "042700": "한미반도체",
    "000990": "DB하이텍", "009150": "삼성전기", "140860": "파크시스템스",
    "058470": "리노공업", "036930": "주성엔지니어링",
    "373220": "LG에너지솔루션", "006400": "삼성SDI", "051910": "LG화학",
    "003670": "포스코퓨처엠", "020150": "롯데에너지머티리얼즈", "005070": "코스모신소재",
    "267260": "HD현대일렉트릭", "010120": "LS일렉트릭", "103590": "일진전기",
    "006260": "LS", "298040": "효성중공업",
    "034020": "두산에너빌리티", "052690": "한전기술", "015760": "한국전력",
    "100090": "SK오션플랜트", "112610": "씨에스윈드",
    "012450": "한화에어로스페이스", "047810": "한국항공우주", "079550": "LIG넥스원",
    "064350": "현대로템", "272210": "한화시스템",
    "009540": "HD한국조선해양", "010140": "삼성중공업", "042660": "한화오션",
    "010620": "HD현대미포", "329180": "HD현대",
    "005380": "현대차", "000270": "기아", "012330": "현대모비스",
    "204320": "HL만도", "011210": "현대위아",
    "207940": "삼성바이오로직스", "068270": "셀트리온", "128940": "한미약품",
    "000100": "유한양행", "302440": "SK바이오사이언스", "326030": "SK바이오팜",
    "035420": "NAVER", "035720": "카카오", "376300": "디어유",
    "036570": "엔씨소프트", "251270": "넷마블", "259960": "크래프톤", "263750": "펄어비스",
    "352820": "하이브", "041510": "에스엠", "122870": "와이지엔터테인먼트",
    "035900": "JYP Ent.",
    "090430": "아모레퍼시픽", "051900": "LG생활건강", "161890": "한국콜마",
    "018250": "애경산업",
    "105560": "KB금융", "055550": "신한지주", "086790": "하나금융지주",
    "316140": "우리금융지주", "138040": "메리츠금융지주",
    "005940": "NH투자증권", "016360": "삼성증권", "006800": "미래에셋증권",
    "071050": "한국금융지주",
    "032830": "삼성생명", "088350": "한화생명", "000810": "삼성화재",
    "005830": "DB손해보험",
    "005490": "POSCO홀딩스", "004020": "현대제철", "103140": "풍산",
    "014820": "동원시스템즈",
    "010950": "S-Oil", "096770": "SK이노베이션", "011170": "롯데케미칼",
    "011790": "SKC", "285130": "SK케미칼",
    "000720": "현대건설", "028050": "삼성E&A", "047040": "대우건설",
    "006360": "GS건설", "375500": "DL이앤씨",
    "003490": "대한항공", "020560": "아시아나항공", "089590": "제주항공",
    "039130": "하나투어",
    "004170": "신세계", "023530": "롯데쇼핑", "069960": "현대백화점",
    "007070": "GS리테일", "097950": "CJ제일제당",
    "017670": "SK텔레콤", "030200": "KT", "032640": "LG유플러스",
    "003550": "LG", "034730": "SK", "000880": "한화", "001040": "CJ", "078930": "GS",
    "454910": "두산로보틱스", "117730": "티로보틱스",
    "271560": "오리온", "004370": "농심", "005300": "롯데칠성", "280360": "롯데웰푸드",
    "011200": "HMM", "028670": "팬오션", "000120": "CJ대한통운", "003280": "흥아해운",
}

_SECTORS = [
    "전기전자", "운수장비", "화학", "의약품", "금융업", "철강금속",
    "기계", "건설업", "유통업", "통신업", "음식료품", "서비스업",
    "운수창고", "비금속광물", "섬유의복", "종이목재", "전기가스업", "보험",
]


def make_snapshot(date: str, stage: str, themes: dict[str, list[str]]) -> dict:
    # 날짜를 시드로 써서 같은 날짜면 같은 결과가 나오게 한다
    rng = random.Random(int(date))

    codes: list[str] = []
    for members in themes.values():
        codes.extend(members)
    codes = sorted(set(codes))

    # 테마별 '분위기' 를 먼저 정하고 종목 수급을 거기에 맞춰 흔든다
    theme_bias = {name: rng.gauss(0, 1) for name in themes}
    code_bias: dict[str, float] = {}
    for name, members in themes.items():
        for c in members:
            code_bias[c] = code_bias.get(c, 0.0) + theme_bias[name]

    flows = []
    for code in codes:
        bias = code_bias.get(code, 0.0)
        scale = rng.choice([30, 80, 200, 600])  # 억원 스케일
        frgn = (bias * 0.6 + rng.gauss(0, 0.7)) * scale * 1e8
        orgn = (bias * 0.4 + rng.gauss(0, 0.6)) * scale * 1e8
        rec = {
            "code": code,
            "name": _NAMES.get(code, f"종목{code}"),
            "price": rng.randrange(5_000, 400_000, 100),
            "chg_pct": round(bias * 1.1 + rng.gauss(0, 1.4), 2),
            "volume": rng.randrange(100_000, 30_000_000),
            "frgn": round(frgn),
            "orgn": round(orgn),
        }
        rec["net"] = rec["frgn"] + rec["orgn"]
        if stage == "final":
            rec["prsn"] = -rec["net"] + round(rng.gauss(0, 0.2) * scale * 1e8)
        flows.append(rec)

    kospi_chg = round(rng.gauss(0.1, 0.9), 2)
    market = {
        "indices": [
            {
                "name": "코스피",
                "value": round(2700 + rng.gauss(0, 120), 2),
                "change": round(2700 * kospi_chg / 100, 2),
                "chg_pct": kospi_chg,
                "volume": rng.randrange(300_000_000, 800_000_000),
                "amount": rng.randrange(8, 16) * 1e12,
                "up": rng.randrange(200, 600),
                "down": rng.randrange(200, 600),
                "flat": rng.randrange(40, 120),
            },
            {
                "name": "코스닥",
                "value": round(760 + rng.gauss(0, 40), 2),
                "change": round(760 * rng.gauss(0, 1) / 100, 2),
                "chg_pct": round(rng.gauss(0, 1.1), 2),
                "volume": rng.randrange(600_000_000, 1_400_000_000),
                "amount": rng.randrange(5, 11) * 1e12,
                "up": rng.randrange(400, 900),
                "down": rng.randrange(400, 900),
                "flat": rng.randrange(60, 160),
            },
        ],
        "sectors": [
            {
                "name": s,
                "chg_pct": round(kospi_chg + rng.gauss(0, 1.3), 2),
                "amount": rng.randrange(1, 40) * 1e11,
                "amount_share": round(rng.uniform(0.5, 18), 2),
            }
            for s in _SECTORS
        ],
    }

    leaders = []
    for code in rng.sample(codes, min(30, len(codes))):
        leaders.append(
            {
                "code": code,
                "name": _NAMES.get(code, f"종목{code}"),
                "chg_pct": round(code_bias.get(code, 0) * 1.2 + rng.gauss(0, 3), 2),
                "amount": rng.randrange(500, 20_000) * 1e8,
                "volume": rng.randrange(500_000, 40_000_000),
                "vol_increase_pct": round(abs(rng.gauss(0, 260)), 1),
                "tags": ["거래대금"],
            }
        )

    us = _sample_us(rng, date)
    return {
        "date": date,
        "stage": stage,
        "collected_at": datetime.now(config.KST).isoformat(timespec="seconds"),
        "amount_unit_detected": "샘플",
        "flows": flows,
        "market": market,
        "volume_leaders": leaders,
        "us": us,
        "explain": _sample_explain(market, us),
    }


def _sample_explain(market: dict, us: dict) -> dict:
    """'왜 움직였나' 화면 확인용. 실제 해설은 src/explain.py 가 만든다.

    구간 판정은 샘플에서도 진짜 계산으로 낸다. 화면에 적힌 등락률과 z 가 어긋나면
    미리보기를 보는 사람이 읽는 법을 잘못 배운다.
    """
    from src.explain import abs_band, classify, vkospi_band

    kospi = next((i for i in market["indices"] if i["name"] == "코스피"), None)
    chg = kospi["chg_pct"] if kospi else 0.0
    v = classify(chg, 4.914)          # 2026-08-28 실측 σ

    def block(index, chg, sigma, z, label, band, verdict, points, conflict, conf):
        vk = {"value": 14.55, "chg_pct": -5.53} if index == "코스피" else None
        return {
            "index": index, "chg_pct": chg, "sigma": sigma, "z": z,
            "label": label, "band": band, "abs_label": abs_band(chg),
            "vkospi": vk, "vkospi_band": vkospi_band(vk["value"]) if vk else "",
            "flow_z": 0.2 if index == "코스피" else None,
            "verdict": verdict, "points": points, "conflict": conflict,
            "confidence": conf, "used": [1, 3],
            "sources": [
                {"title": "코스피, 반도체 강세에 상승 마감", "source": "샘플뉴스",
                 "time": "16:05", "url": "https://example.com/1"},
                {"title": "외국인 순매수 이틀째…반도체 집중", "source": "샘플경제",
                 "time": "16:40", "url": "https://example.com/2"},
                {"title": "美 관세 우려에 코스피 낙폭 확대", "source": "샘플일보",
                 "time": "17:10", "url": "https://example.com/3"},
            ],
        }

    top = sorted(market["sectors"], key=lambda x: x["chg_pct"], reverse=True)
    real = [x for x in top if x["name"] not in ("종합", "대형주", "중형주", "소형주", "VKOSPI")]
    picks = real[:10] + real[-10:]
    sector_rows = [
        {
            "name": x["name"], "chg_pct": x["chg_pct"],
            "rank": "top" if i < 10 else "bottom",
            "line": (f"{x['name']} 는 구성 종목 중 상위 몇 개가 등락을 끌었습니다."
                     if i % 3 else ""),
            "sources": [1, 4] if i % 3 == 1 else [],
        }
        for i, x in enumerate(picks)
    ]

    return {
        "model": "claude-sonnet-5",
        "sectors": {
            "kr": {
                "note": "반도체에서 빠진 돈이 화학·음식료 같은 내수 업종으로 옮겨 간 하루로 보입니다.",
                "rows": sector_rows,
            },
            "us": {
                "note": "엔비디아 실적에 반도체·기술만 오르고 방어 업종은 밀린 하루로 보입니다.",
                "rows": [
                    {
                        "name": x["name"], "chg_pct": x["chg_pct"],
                        "rank": "top" if i < len(us["sectors"]) // 2 else "bottom",
                        "line": (f"{x['name']} 는 주요 종목이 등락을 끌었습니다." if i % 2 else ""),
                        "sources": [2] if i % 2 else [],
                    }
                    for i, x in enumerate(us["sectors"])
                ],
            },
        },
        "markets": {
            "kr": block(
                "코스피", chg, v["sigma"], v["z"], v["label"], v["band"],
                "최근 변동성 기준으로는 평범한 하루입니다. 특별한 이유를 찾기 어렵습니다.",
                [
                    f"요즘 코스피는 하루 표준편차가 4.9%인 국면이라, 오늘의 {chg:+.2f}%는 그 {v['z']}배 수준입니다 (데이터).",
                    "외국인·기관이 함께 순매수였지만 규모는 최근 평균 범위 안입니다 (데이터).",
                    "기사 다수가 반도체 강세를 이유로 들지만, 그 정도 움직임은 이 시장에서 흔합니다 (기사 1·3).",
                ],
                "일부 기사는 관세 우려로 낙폭이 확대됐다고 썼지만 지수는 상승 마감했습니다.",
                "낮음",
            ),
            "us": block(
                "S&P 500", -0.02, 0.646, 0.03, "조용", "quiet",
                "사실상 보합입니다. 엔비디아 실적 발표를 앞둔 관망세로 보입니다.",
                [
                    "S&P 500 변동폭이 최근 표준편차의 3% 수준으로, 방향성이 없는 하루였습니다 (데이터).",
                    "섹터도 상하위 폭이 좁아 특정 업종이 끌고 간 흔적이 없습니다 (데이터).",
                    "기사들은 공통적으로 실적 대기 관망을 이유로 듭니다 (기사 1).",
                ],
                "",
                "낮음",
            ),
        },
    }


def _sample_us(rng: random.Random, date: str) -> dict:
    """미국증시 탭 화면 확인용. 실제 수집은 src/overseas.py 가 한다."""
    from src import overseas

    mood = rng.gauss(0, 0.8)          # 그날의 시장 분위기 하나로 전체를 흔든다
    def move(base: float, beta: float, noise: float) -> tuple[float, float]:
        pct = round(mood * beta + rng.gauss(0, noise), 2)
        return round(base * (1 + pct / 100), 2), pct

    def walk(last: float, n: int = 30) -> list[list]:
        """그래프 확인용 가짜 시계열. 마지막 값에서 거꾸로 흔들어 올라간다."""
        out, v = [], last
        for i in range(n):
            out.append(round(v, 4))
            v = v / (1 + rng.gauss(0, 0.012))
        days = [f"{date[:4]}-{date[4:6]}-{max(1, int(date[6:]) - i):02d}" for i in range(n)]
        return [[d, x] for d, x in zip(days[::-1], out[::-1])]

    indices = []
    for name, base, beta in (("S&P 500", 6412.55, 1.0), ("나스닥", 21204.13, 1.4), ("다우", 44180.20, 0.7)):
        value, pct = move(base, beta, 0.4)
        indices.append(
            {"name": name, "value": value, "change": round(value * pct / 100, 2), "chg_pct": pct}
        )

    vix, vix_pct = move(15.8, -4.0, 3.0)      # 지수와 반대로 움직인다
    fx, fx_pct = move(1352.4, -0.15, 0.25)
    y10, y10_pct = move(4.28, 0.1, 0.9)
    macro = [
        {"name": "VIX", "value": vix, "change": round(vix * vix_pct / 100, 2), "chg_pct": vix_pct, "unit": "pt"},
        {"name": "원/달러", "value": fx, "change": round(fx * fx_pct / 100, 2), "chg_pct": fx_pct, "unit": "won"},
        {"name": "미 10년물", "value": y10, "change": round(y10 * y10_pct / 100, 3),
         "chg_pct": y10_pct, "unit": "yield", "note": "야후 시세", "series": walk(y10)},
    ]

    sectors = []
    for symbol, name in overseas.SECTORS:
        beta = 1.6 if symbol in ("XLK", "SMH") else 0.4 if symbol in ("XLP", "XLU") else 1.0
        value, pct = move(rng.randrange(30, 260) + 0.5, beta, 0.9)
        sectors.append({"symbol": symbol, "name": name, "value": value, "chg_pct": pct})
    sectors.sort(key=lambda x: x["chg_pct"], reverse=True)

    stocks = []
    for symbol, name in overseas.WATCH:
        beta = 1.8 if symbol in ("NVDA", "AMD", "MU", "AVGO", "TSM", "ARM") else 1.0
        value, pct = move(rng.randrange(40, 900) + 0.5, beta, 1.6)
        stocks.append({"symbol": symbol, "name": name, "value": value, "chg_pct": pct})
    stocks.sort(key=lambda x: x["chg_pct"], reverse=True)

    wti, wti_pct = move(63.4, 0.5, 1.4)
    dxy, dxy_pct = move(98.2, -0.2, 0.3)
    btc, btc_pct = move(110_898_000, 1.5, 2.2)
    extras = [
        {"key": "wti", "name": "국제유가 (WTI)", "unit": "usd",
         "value": wti, "change": round(wti * wti_pct / 100, 2), "chg_pct": wti_pct,
         "note": "", "series": walk(wti)},
        {"key": "dxy", "name": "달러지수", "unit": "pt",
         "value": dxy, "change": round(dxy * dxy_pct / 100, 2), "chg_pct": dxy_pct,
         "note": "", "series": walk(dxy)},
        {"key": "btc", "name": "비트코인", "unit": "krw",
         "value": round(btc), "change": round(btc * btc_pct / 100), "chg_pct": btc_pct,
         "note": "업비트 · 전일 대비", "series": walk(btc)},
    ]

    prev = f"{date[:4]}-{date[4:6]}-{int(date[6:]) - 1:02d}"   # 현지는 하루 전
    return {
        "as_of": prev, "indices": indices, "macro": macro,
        "sectors": sectors, "stocks": stocks, "extras": extras,
    }
