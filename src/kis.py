"""
한국투자증권 KIS Open API REST 클라이언트.

- 접근토큰을 파일에 캐시한다 (KIS 토큰은 24시간 유효하고, 하루 재발급 횟수 제한이 있음).
- 초당 호출수를 스스로 제한한다.
- 일시적 오류(429/5xx/네트워크)는 지수 백오프로 재시도한다.

여기서 쓰는 엔드포인트와 tr_id 는 한국투자증권 공식 예제
(github.com/koreainvestment/open-trading-api) 기준입니다.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import requests

import config

log = logging.getLogger(__name__)

TOKEN_CACHE = config.CACHE_DIR / f"kis_token_{config.KIS_ENV}.json"


class KisError(RuntimeError):
    """KIS API 가 rt_cd != '0' 으로 응답했을 때."""

    def __init__(self, msg_cd: str, msg: str, tr_id: str):
        super().__init__(f"[{tr_id}] {msg_cd}: {msg}")
        self.msg_cd = msg_cd
        self.msg = msg
        self.tr_id = tr_id


@dataclass
class _RateLimiter:
    per_sec: int
    _lock: threading.Lock = threading.Lock()
    _slots: list = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self._slots = []

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            self._slots = [t for t in self._slots if now - t < 1.0]
            if len(self._slots) >= self.per_sec:
                time.sleep(1.0 - (now - self._slots[0]) + 0.01)
                now = time.monotonic()
                self._slots = [t for t in self._slots if now - t < 1.0]
            self._slots.append(time.monotonic())


class KisClient:
    def __init__(
        self,
        app_key: str | None = None,
        app_secret: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.app_key = app_key or config.KIS_APP_KEY
        self.app_secret = app_secret or config.KIS_APP_SECRET
        self.base_url = base_url or config.KIS_BASE_URL
        if not self.app_key or not self.app_secret:
            raise RuntimeError(
                "KIS_APP_KEY / KIS_APP_SECRET 이 비어 있습니다. "
                ".env 파일이나 GitHub Secrets 에 넣어주세요."
            )
        self._session = requests.Session()
        # 여러 스레드가 동시에 부를 때 기본 풀(10)에서 줄서지 않도록 넉넉히 잡는다.
        # 실제 호출 속도는 아래 _RateLimiter 가 초당 건수로 제한한다.
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=config.KIS_RATE_LIMIT_PER_SEC * 2,
            pool_maxsize=config.KIS_RATE_LIMIT_PER_SEC * 2,
        )
        self._session.mount("https://", adapter)
        self._limiter = _RateLimiter(config.KIS_RATE_LIMIT_PER_SEC)
        self._token: str | None = None

    # ------------------------------------------------------------ 인증
    def _load_cached_token(self) -> str | None:
        if not TOKEN_CACHE.exists():
            return None
        try:
            blob = json.loads(TOKEN_CACHE.read_text())
        except (json.JSONDecodeError, OSError):
            return None
        if blob.get("app_key") != self.app_key:
            return None
        expires = datetime.fromisoformat(blob["expires_at"])
        # 만료 10분 전이면 새로 받는다
        if datetime.now() >= expires - timedelta(minutes=10):
            return None
        return blob["token"]

    def _save_token(self, token: str, expires_in: int) -> None:
        TOKEN_CACHE.write_text(
            json.dumps(
                {
                    "app_key": self.app_key,
                    "token": token,
                    "expires_at": (
                        datetime.now() + timedelta(seconds=expires_in)
                    ).isoformat(),
                }
            )
        )

    @property
    def token(self) -> str:
        if self._token:
            return self._token
        cached = self._load_cached_token()
        if cached:
            self._token = cached
            return cached

        log.info("KIS 접근토큰 발급 중...")
        res = self._session.post(
            f"{self.base_url}/oauth2/tokenP",
            json={
                "grant_type": "client_credentials",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
            },
            headers={"content-type": "application/json; charset=utf-8"},
            timeout=20,
        )
        res.raise_for_status()
        body = res.json()
        if "access_token" not in body:
            raise RuntimeError(f"토큰 발급 실패: {body}")
        self._token = body["access_token"]
        self._save_token(self._token, int(body.get("expires_in", 86400)))
        log.info("접근토큰 발급 완료")
        return self._token

    # ------------------------------------------------------------ 호출
    def get(
        self,
        path: str,
        tr_id: str,
        params: dict[str, str],
        *,
        tr_cont: str = "",
        retries: int = 4,
    ) -> dict[str, Any]:
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.token}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "tr_cont": tr_cont,
            "custtype": "P",  # 개인
        }
        last_exc: Exception | None = None
        for attempt in range(retries):
            self._limiter.wait()
            try:
                res = self._session.get(
                    f"{self.base_url}{path}",
                    headers=headers,
                    params=params,
                    timeout=25,
                )
            except requests.RequestException as exc:  # 네트워크 계열
                last_exc = exc
                time.sleep(2**attempt)
                continue

            if res.status_code in (429, 500, 502, 503, 504):
                last_exc = RuntimeError(f"HTTP {res.status_code}: {res.text[:200]}")
                time.sleep(2**attempt)
                continue
            res.raise_for_status()

            body = res.json()
            rt_cd = body.get("rt_cd")
            if rt_cd == "0":
                return body

            msg_cd = body.get("msg_cd", "")
            msg = body.get("msg1", "").strip()
            # 초당 거래건수 초과 -> 잠시 쉬고 재시도
            if msg_cd in ("EGW00201", "EGW00133"):
                last_exc = KisError(msg_cd, msg, tr_id)
                time.sleep(1.0 + attempt)
                continue
            # 토큰 만료 -> 한 번 갱신하고 재시도
            if msg_cd in ("EGW00121", "EGW00123"):
                log.warning("토큰 만료로 재발급합니다.")
                self._token = None
                TOKEN_CACHE.unlink(missing_ok=True)
                headers["authorization"] = f"Bearer {self.token}"
                last_exc = KisError(msg_cd, msg, tr_id)
                continue
            raise KisError(msg_cd, msg, tr_id)

        raise RuntimeError(f"{tr_id} 호출 실패 ({retries}회 시도): {last_exc}")

    # ------------------------------------------------------------ 개별 API
    def foreign_institution_total(
        self,
        *,
        market: str = "0000",   # 0000:전체 0001:코스피 1001:코스닥
        sort: str = "0",        # 0:순매수상위 1:순매도상위
        by_amount: bool = True, # 금액정렬 여부
        who: str = "0",         # 0:전체 1:외국인 2:기관계 3:기타
    ) -> list[dict[str, str]]:
        """국내기관·외국인 매매종목 가집계 [국내주식-037] (tr_id FHPTJ04400000).

        종목별 외국인/기관 순매수 대금·수량을 순위로 돌려준다.
        수급 분석의 주력 데이터.
        """
        body = self.get(
            "/uapi/domestic-stock/v1/quotations/foreign-institution-total",
            "FHPTJ04400000",
            {
                "FID_COND_MRKT_DIV_CODE": "V",
                "FID_COND_SCR_DIV_CODE": "16449",
                "FID_INPUT_ISCD": market,
                "FID_DIV_CLS_CODE": "1" if by_amount else "0",
                "FID_RANK_SORT_CLS_CODE": sort,
                "FID_ETC_CLS_CODE": who,
            },
        )
        return body.get("output") or []

    def index_category_price(self, market_cls: str = "K") -> list[dict[str, str]]:
        """국내업종 구분별 전체시세 [국내주식-066] (tr_id FHPUP02140000).

        market_cls: K=거래소(코스피), Q=코스닥, K2=코스피200
        업종별 등락률·거래대금을 한 번에 준다.
        """
        iscd = {"K": "0001", "Q": "1001", "K2": "2001"}[market_cls]
        body = self.get(
            "/uapi/domestic-stock/v1/quotations/inquire-index-category-price",
            "FHPUP02140000",
            {
                "FID_COND_MRKT_DIV_CODE": "U",
                "FID_INPUT_ISCD": iscd,
                "FID_COND_SCR_DIV_CODE": "20214",
                "FID_MRKT_CLS_CODE": market_cls,
                "FID_BLNG_CLS_CODE": "0",  # 전업종
            },
        )
        return body.get("output2") or []

    def index_price(self, iscd: str = "0001") -> dict[str, str]:
        """업종(지수) 현재가. 0001=코스피 종합, 1001=코스닥."""
        body = self.get(
            "/uapi/domestic-stock/v1/quotations/inquire-index-price",
            "FHPUP02100000",
            {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": iscd},
        )
        return body.get("output") or {}

    def volume_rank(
        self,
        *,
        belong: str = "3",  # 0:평균거래량 1:거래증가율 2:평균거래회전율 3:거래금액순
        market: str = "0000",
        min_vol: str = "100000",
    ) -> list[dict[str, str]]:
        """거래량순위 [국내주식-047] (tr_id FHPST01710000)."""
        body = self.get(
            "/uapi/domestic-stock/v1/quotations/volume-rank",
            "FHPST01710000",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_COND_SCR_DIV_CODE": "20171",
                "FID_INPUT_ISCD": market,
                "FID_DIV_CLS_CODE": "0",
                "FID_BLNG_CLS_CODE": belong,
                "FID_TRGT_CLS_CODE": "111111111",
                "FID_TRGT_EXLS_CLS_CODE": "0000000000",
                "FID_INPUT_PRICE_1": "",
                "FID_INPUT_PRICE_2": "",
                "FID_VOL_CNT": min_vol,
                "FID_INPUT_DATE_1": "",
            },
        )
        return body.get("output") or []

    def investor_trade_by_stock_daily(
        self, code: str, date: str
    ) -> list[dict[str, str]]:
        """종목별 투자자매매동향(일별) (tr_id FHPTJ04160001).

        개인 순매수까지 포함된 확정치. 종목당 1콜이라 관심종목에만 쓴다.
        date: YYYYMMDD
        """
        body = self.get(
            "/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily",
            "FHPTJ04160001",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": code,
                "FID_INPUT_DATE_1": date,
                "FID_ORG_ADJ_PRC": "",
                "FID_ETC_CLS_CODE": "",
            },
        )
        return body.get("output2") or body.get("output") or []

    def is_holiday(self, date: str) -> bool | None:
        """국내휴장일 조회. 개장일이면 False, 휴장이면 True, 판단 불가 시 None."""
        try:
            body = self.get(
                "/uapi/domestic-stock/v1/quotations/chk-holiday",
                "CTCA0903R",
                {"BASS_DT": date, "CTX_AREA_NK": "", "CTX_AREA_FK": ""},
            )
        except Exception as exc:  # 이 API 가 막혀도 파이프라인은 계속 돌아야 한다
            log.warning("휴장일 조회 실패(무시): %s", exc)
            return None
        for row in body.get("output") or []:
            if row.get("bass_dt") == date:
                return row.get("opnd_yn") != "Y"
        return None
