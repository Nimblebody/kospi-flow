"""
증시 뉴스 모음과 요약.

하루치 기사를 모아 (1) 한 편의 요약과 (2) 중요 기사 목록을 만든다.
수급 리포트와는 따로 돌고 따로 저장한다. 뉴스가 실패해도 리포트는 그대로 나간다.

기사 출처를 두 갈래로 둔다.
  1) 국내 언론사 RSS  — 링크가 기사 주소 그대로다. 이쪽을 먼저 쓴다.
  2) 구글 뉴스 RSS    — 출처가 넓어 빈자리를 메운다. 다만 링크가
     news.google.com 중간 페이지라 한 번 더 튄다(진짜 주소는 서버에서 못 뽑는다.
     base64 디코딩·본문 추출 모두 실패했다).

종합 피드에는 스포츠·연예가 섞여 들어와서 제목 키워드로 거른다.
"""
from __future__ import annotations

import html
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

import requests

import config
from src.explain import _ask, headlines

log = logging.getLogger(__name__)

# 링크가 기사 주소 그대로인 피드. 2026-09-02 에 응답을 확인했다.
# 막히는 곳(매일경제 403, 이데일리 연결끊김, 서울경제/파이낸셜 404)은 뺐다.
FEEDS = [
    ("연합뉴스", "https://www.yna.co.kr/rss/economy.xml"),
    ("한국경제", "https://www.hankyung.com/feed/finance"),
    ("한국경제", "https://www.hankyung.com/feed/economy"),
    ("머니투데이", "https://rss.mt.co.kr/mt_news.xml"),
    ("아시아경제", "https://www.asiae.co.kr/rss/stock.htm"),
    ("뉴시스", "https://newsis.com/RSS/economy.xml"),
    ("조선비즈", "https://biz.chosun.com/arc/outboundfeeds/rss/category/stock/?outputType=xml"),
    ("연합인포맥스", "https://news.einfomax.co.kr/rss/S1N2.xml"),
]

# 구글 뉴스로 메울 때 쓰는 검색어. 국내 증시로 좁힌다.
GOOGLE_QUERIES = [
    "코스피", "코스닥", "증시 마감", "외국인 순매수",
    "코스피 종목", "국내 증시 전망",
]

# 종합 피드에서 증시 기사만 남기는 실마리. 하나라도 걸리면 남긴다.
STOCK_HINTS = (
    "코스피", "코스닥", "증시", "주가", "주식", "종목", "상장", "공모",
    "외국인", "기관", "수급", "매수", "매도", "시총", "시가총액",
    "실적", "영업이익", "배당", "자사주", "반도체", "이차전지", "2차전지",
    "나스닥", "다우", "뉴욕증시", "환율", "금리", "채권", "ETF", "펀드",
)

# 모델에 넣을 기사 수와 목록에 올릴 기사 수
POOL = 60
TOP_N = 20


def _clean(title: str) -> str:
    """제목 끝에 붙는 ' - 매체명' 을 떼고, HTML 엔티티를 풀고, 공백을 고른다.

    RSS 제목에는 &quot; &amp; 가 그대로 실려 온다. 안 풀면 화면에 그대로 보인다.
    """
    t = html.unescape(title)
    t = re.sub(r"\s+-\s+[^-]{2,20}$", "", t).strip()
    return re.sub(r"\s+", " ", t)


def _key(title: str) -> str:
    """같은 사건을 다룬 기사를 묶기 위한 열쇠. 기호와 공백을 지운 제목."""
    return re.sub(r"[^0-9A-Za-z가-힣]", "", _clean(title))[:40]


def _is_stock(title: str) -> bool:
    return any(h in title for h in STOCK_HINTS)


def _from_feeds(since: datetime) -> list[dict]:
    """국내 언론사 RSS. 링크가 기사 주소 그대로다."""
    out: list[dict] = []
    for source, url in FEEDS:
        try:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
            r.raise_for_status()
            root = ET.fromstring(r.content)
        except Exception as exc:
            log.warning("피드 실패 %s (%s): %s", source, url[:40], exc)
            continue

        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            if not title or not link.startswith("http"):
                continue
            try:
                when = parsedate_to_datetime(item.findtext("pubDate") or "")
                kst = when.astimezone(config.KST)
            except Exception:
                continue
            if kst < since or not _is_stock(title):
                continue
            out.append({
                "title": _clean(title),
                "source": source,
                "time": kst.strftime("%m-%d %H:%M"),
                "at": kst,
                "url": link,
                "direct": True,
            })
    return out


def _from_google(day: str) -> list[dict]:
    """구글 뉴스로 빈자리를 메운다. 링크는 중간 페이지를 거친다."""
    out = []
    for a in headlines(GOOGLE_QUERIES, day, limit=200):
        title = _clean(a["title"])
        if not _is_stock(title):
            continue
        try:
            at = datetime.strptime(f"{day} {a['time']}", "%Y-%m-%d %H:%M").replace(
                tzinfo=config.KST
            )
        except ValueError:
            continue
        out.append({
            "title": title,
            "source": a["source"] or "구글 뉴스",
            "time": at.strftime("%m-%d %H:%M"),
            "at": at,
            "url": a["url"],
            "direct": False,
        })
    return out


def collect(day: str) -> list[dict]:
    """day(YYYY-MM-DD, KST) 00:00 이후 기사를 모은다. 직접링크를 앞에 둔다."""
    since = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=config.KST)
    rows = _from_feeds(since) + _from_google(day)

    # 같은 사건이면 직접링크를 남긴다. 그다음은 이른 기사.
    best: dict[str, dict] = {}
    for r in sorted(rows, key=lambda x: (not x["direct"], x["at"])):
        best.setdefault(_key(r["title"]), r)

    out = sorted(best.values(), key=lambda x: x["at"], reverse=True)
    log.info(
        "뉴스 %d건 수집 (직접링크 %d · 구글 %d)",
        len(out), sum(1 for r in out if r["direct"]), sum(1 for r in out if not r["direct"]),
    )
    for r in out:
        r.pop("at", None)
    return out


# ---------------------------------------------------------------- 요약
# 모델에게 제목을 다시 쓰게 하지 않는다. 번호만 고르게 하고 실제 기사와는
# 파이썬이 맞춘다. 제목을 지어내는 일을 원천적으로 막는다.
# 모든 객체에 additionalProperties: False 가 있어야 한다.
# 빠뜨렸더니 API 가 스키마를 거부했다(2026-09-02 첫 실행 실패).
# explain.py 의 SECTOR_SCHEMA 와 같은 모양으로 맞춘다.
SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "points": {"type": "array", "items": {"type": "string"}},
        "top": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "why": {"type": "string"},
                },
                "required": ["index", "why"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["headline", "points", "top"],
    "additionalProperties": False,
}


def _prompt(day: str, rows: list[dict]) -> str:
    listing = "\n".join(
        f"{i}. [{r['time']}] ({r['source']}) {r['title']}" for i, r in enumerate(rows, 1)
    )
    return f"""아래는 {day} 국내 증시 관련 기사 제목 {len(rows)}건이다. 제목만 있고 본문은 없다.

{listing}

할 일 두 가지.

1) headline / points
   그날 증시 뉴스를 읽는 사람에게 한 편으로 정리해 준다.
   - headline: 그날을 한 문장으로. 40자 안팎.
   - points: 4~6개. 각 항목은 한두 문장. 무슨 일이 있었고 왜 중요한지 적는다.
     비슷한 기사가 여러 건이면 하나로 묶어서 말한다.

2) top
   위 목록에서 중요한 기사 {TOP_N}건을 골라 번호(index)와 고른 이유(why)를 준다.
   - 중요도 순으로 정렬한다.
   - why 는 한 문장. 25자 안팎.
   - 같은 사건을 다룬 기사는 하나만 고른다.
   - 광고성·단순 시황 반복·개별 종목 홍보성 기사는 뺀다.

지켜야 할 것.
- 제목에 없는 사실을 지어내지 않는다. 본문을 못 봤으므로 제목이 말하는 범위 안에서만 쓴다.
- 확실하지 않으면 단정하지 말고 '~로 보인다' 처럼 적는다.
- index 는 반드시 1~{len(rows)} 사이의 실제 번호여야 한다.
- 한국어로 쓴다. 문장은 마침표로 끝낸다."""


def summarize(day: str, rows: list[dict]) -> dict | None:
    """제목 목록을 넣고 요약과 중요 기사 번호를 받는다."""
    pool = rows[:POOL]
    if len(pool) < 10:
        log.warning("기사가 %d건뿐이라 요약을 만들지 않습니다.", len(pool))
        return None

    log.info("요약 요청: 기사 %d건", len(pool))
    try:
        got = _ask(_prompt(day, pool), SCHEMA)
    except Exception as exc:
        log.warning("요약 실패: %s", exc)
        return None
    if not got:
        return None

    # 번호를 실제 기사로 바꾼다. 범위를 벗어나거나 중복된 번호는 버린다.
    top, seen = [], set()
    for item in got.get("top") or []:
        i = item.get("index")
        if not isinstance(i, int) or not (1 <= i <= len(pool)) or i in seen:
            continue
        seen.add(i)
        top.append({**pool[i - 1], "why": (item.get("why") or "").strip()})

    if len(top) < len(got.get("top") or []):
        log.warning("모델이 준 번호 중 %d개를 버렸습니다.", len(got.get("top") or []) - len(top))

    return {
        "headline": (got.get("headline") or "").strip(),
        "points": [p.strip() for p in (got.get("points") or []) if p.strip()],
        "top": top[:TOP_N],
    }


def build(day: str) -> dict | None:
    """그날 뉴스 리포트 한 벌."""
    rows = collect(day)
    if not rows:
        log.warning("%s 기사를 하나도 못 모았습니다.", day)
        return None

    summary = summarize(day, rows)
    if not summary:
        return None

    return {
        "date": day,
        "generated_at": datetime.now(config.KST).isoformat(timespec="seconds"),
        "collected": len(rows),
        "pool": min(len(rows), POOL),
        "headline": summary["headline"],
        "points": summary["points"],
        "top": summary["top"],
    }
