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

    return {
        "date": date,
        "stage": stage,
        "collected_at": datetime.now(config.KST).isoformat(timespec="seconds"),
        "amount_unit_detected": "샘플",
        "flows": flows,
        "market": market,
        "volume_leaders": leaders,
    }
