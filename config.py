"""
전역 설정.

민감한 값(앱키/앱시크릿/텔레그램 토큰)은 코드에 적지 말고
환경변수 또는 .env 파일로 넣으세요. .env 는 .gitignore 에 포함되어 있습니다.
"""
from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------- 경로
ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
DATA_DIR = WEB_DIR / "data"
CACHE_DIR = ROOT / ".cache"

for _d in (WEB_DIR, DATA_DIR, CACHE_DIR):
    _d.mkdir(parents=True, exist_ok=True)

KST = ZoneInfo("Asia/Seoul")


# ---------------------------------------------------------------- .env 로딩
def _load_dotenv() -> None:
    """의존성 없이 .env 를 읽어 os.environ 에 채운다 (이미 있는 값은 유지)."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        os.environ.setdefault(key, val)


_load_dotenv()


# ---------------------------------------------------------------- KIS
# 실전투자: prod / 모의투자: vps
KIS_ENV = os.getenv("KIS_ENV", "prod").lower()

KIS_BASE_URL = {
    "prod": "https://openapi.koreainvestment.com:9443",
    "vps": "https://openapivts.koreainvestment.com:29443",
}[KIS_ENV]

KIS_APP_KEY = os.getenv("KIS_APP_KEY", "")
KIS_APP_SECRET = os.getenv("KIS_APP_SECRET", "")

# 실전 초당 20건 / 모의 초당 1건. 여유를 두고 잡는다.
KIS_RATE_LIMIT_PER_SEC = 8 if KIS_ENV == "prod" else 1

# 마스터 파일 (종목/테마) 다운로드 경로
MASTER_URLS = {
    "kospi": "https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip",
    "theme": "https://new.real.download.dws.co.kr/common/master/theme_code.mst.zip",
}
MASTER_TTL_DAYS = 7  # 마스터 파일 재다운로드 주기

# 마스터 파일 서버로 나가는 길이 막힌 환경에서는 1 로 두고 내장 테마 사전을 쓴다
SKIP_MASTER_DOWNLOAD = os.getenv("SKIP_MASTER_DOWNLOAD", "0") == "1"


# ---------------------------------------------------------------- 텔레그램
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


def _chat_ids(raw: str) -> list[str]:
    """여러 명에게 보낼 수 있게 쉼표로 나눈다.

    텔레그램 sendMessage 는 한 번에 한 사람이라, 받는 쪽이 늘면 그만큼 호출한다.
    시크릿을 여러 개 만드는 대신 한 칸에 나열한다 (예: "12345,-1009876").
    그룹 방 아이디는 음수라 그대로 들어간다.
    """
    return [x.strip() for x in raw.replace(";", ",").split(",") if x.strip()]


TELEGRAM_CHAT_IDS = _chat_ids(TELEGRAM_CHAT_ID)

# 대시보드 공개 주소 (GitHub Pages). 알림 메시지의 링크로 쓰인다.
SITE_URL = os.getenv("SITE_URL", "")


# ---------------------------------------------------------------- 분석 파라미터
# 테마 순위에 올리기 위한 최소 구성종목 수 (1종목짜리 테마는 노이즈)
MIN_THEME_MEMBERS = 2

# ── 테마 대표성 ────────────────────────────────────────────────
# 수급 API 가 상위 30종목만 주는 탓에, 대형주 하나가 여러 테마의 순위를
# 동시에 끌어올린다. 아래 두 기준을 통과한 테마만 화면 상단에 올린다.
# (전체 테마 목록 자체는 그대로 두고, '보여주는 자리' 에서만 거른다)

# 1등 종목이 테마 순매수(절대값 합)의 이 비율 이상을 차지하면 테마가 아니라 종목이다
THEME_MAX_TOP1_SHARE = 0.5

# 수급이 잡힌 구성종목이 이보다 적으면 테마 전체의 흐름이라 부를 수 없다
THEME_MIN_DATA_MEMBERS = 3


# ---------------------------------------------------------------- 보관
# 이 기간 안의 리포트는 전체를 남겨 화면에서 그날로 되돌아가 볼 수 있게 한다.
# 지나면 자금 흐름 시계열만 남기고 줄인다 (store.slim).
#
# 용량 때문이 아니다. 실측상 전체를 계속 저장해도 git 기준 연 9MB 수준이고,
# 이미 커밋된 파일을 나중에 줄여도 .git 은 작아지지 않는다 (예전 버전이 남는다).
# 화면에서 되돌아가 볼 수 있는 범위를 정하는 값이다.
FULL_REPORT_DAYS = 125  # 약 6개월(영업일 기준)

# 화면/알림에 보여줄 개수
TOP_THEMES = 8
TOP_STOCKS_PER_THEME = 5
TOP_MOVERS = 12

# 업종 카드를 눌렀을 때 펼쳐 보여줄 종목 수
SECTOR_STOCKS = 10

# 자금 이동 패널. 유입 증가/감소 테마를 각각 이 개수만큼 뽑고,
# 그 둘을 크기순으로 짝지어 이동 경로를 만든다.
# 경로 개수는 짝짓기 결과에 달렸으므로 상한만 둔다.
ROTATION_TOP_THEMES = 10
ROTATION_MAX_PAIRS = 13

# 거래대금 급증 판정: 최근 평균 대비 배수
VOLUME_SPIKE_RATIO = 2.0
