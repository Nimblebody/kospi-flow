"""
종목 마스터 / 테마 마스터.

테마 분류는 한국투자증권이 공개하는 테마 마스터 파일을 1순위로 쓴다.
  https://new.real.download.dws.co.kr/common/master/theme_code.mst.zip
  (테마코드 3자리 + 테마명 + 종목코드 6자리, cp949)

다운로드가 막힌 환경(사내망, 일부 CI)에서는 fallback_themes.py 의
수기 테마 사전으로 자동 전환한다.
"""
from __future__ import annotations

import io
import logging
import time
import zipfile
from pathlib import Path

import requests

import config
from src.fallback_themes import FALLBACK_THEMES

log = logging.getLogger(__name__)


def _download(url: str, dest: Path) -> bool:
    """마스터 zip 을 받아서 풀어둔다. 성공하면 True."""
    if config.SKIP_MASTER_DOWNLOAD:
        return dest.exists()
    fresh_for = config.MASTER_TTL_DAYS * 86400
    if dest.exists() and time.time() - dest.stat().st_mtime < fresh_for:
        return True
    try:
        res = requests.get(url, timeout=60, verify=False)
        res.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
            name = zf.namelist()[0]
            dest.write_bytes(zf.read(name))
        log.info("마스터 파일 갱신: %s (%d bytes)", dest.name, dest.stat().st_size)
        return True
    except Exception as exc:
        log.warning("마스터 다운로드 실패 %s: %s", url, exc)
        return dest.exists()  # 예전에 받아둔 게 있으면 그거라도 쓴다


def load_stock_names() -> dict[str, str]:
    """{종목코드: 한글종목명} — 코스피 종목 마스터."""
    dest = config.CACHE_DIR / "kospi_code.mst"
    if not _download(config.MASTER_URLS["kospi"], dest):
        return {}
    names: dict[str, str] = {}
    with dest.open(encoding="cp949", errors="replace") as f:
        for row in f:
            head = row[: len(row) - 228]
            code = head[0:9].strip()
            name = head[21:].strip()
            if len(code) == 6 and name:
                names[code] = name
    log.info("코스피 종목 마스터 %d 종목", len(names))
    return names


def load_themes() -> tuple[dict[str, list[str]], str]:
    """{테마명: [종목코드,...]} 와 출처 문자열을 돌려준다."""
    dest = config.CACHE_DIR / "theme_code.mst"
    if _download(config.MASTER_URLS["theme"], dest):
        themes: dict[str, list[str]] = {}
        with dest.open(encoding="cp949", errors="replace") as f:
            for row in f:
                row = row.rstrip("\n")
                if len(row) < 14:
                    continue
                code = row[-10:].strip()
                name = row[3:-10].strip()
                if not name or len(code) != 6:
                    continue
                themes.setdefault(name, [])
                if code not in themes[name]:
                    themes[name].append(code)
        if themes:
            log.info("KIS 테마 마스터 %d개 테마", len(themes))
            return themes, "KIS 테마 마스터"

    log.warning("KIS 테마 마스터를 못 받아 내장 테마 사전으로 대체합니다.")
    return {k: list(v) for k, v in FALLBACK_THEMES.items()}, "내장 테마 사전"


def build_stock_to_themes(themes: dict[str, list[str]]) -> dict[str, list[str]]:
    """{종목코드: [테마명,...]} 역인덱스."""
    idx: dict[str, list[str]] = {}
    for theme, codes in themes.items():
        for code in codes:
            idx.setdefault(code, []).append(theme)
    return idx
