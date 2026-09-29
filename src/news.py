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

조선 탭은 네이버 검색(API HUB)을 쓴다. 원문 링크와 앞부분 요약이 같이 온다.
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


ALIKE = 0.5   # 제목 두 글자 조각이 이만큼 겹치면 같은 소식으로 본다


def _grams(title: str) -> set[str]:
    """제목의 두 글자 조각. 머리말([속보]·[특징주] 등)은 뺀다."""
    k = _key(re.sub(r"\[[^\]]*\]", "", title))
    return {k[i:i + 2] for i in range(len(k) - 1)}


def _alike(a: set[str], b: set[str]) -> bool:
    """짧은 쪽 제목 조각의 절반 이상이 겹치면 같은 소식.

    같은 공시를 매체마다 제목을 조금씩 바꿔 쓴다. 9/28 조선 기사 50건 중 23건이
    한화오션 LNG선 수주 한 건이었는데, 제목이 달라 _key 로는 하나도 안 묶였다.
    0.4 로 낮추면 같은 날 상장한 서로 다른 두 종목 기사가 한데 묶였다(뉴스 탭).
    """
    return bool(a and b) and len(a & b) / min(len(a), len(b)) >= ALIKE


def _is_stock(title: str) -> bool:
    return any(h in title for h in STOCK_HINTS)


def _from_feeds(since: datetime, keep=None) -> list[dict]:
    """국내 언론사 RSS. 링크가 기사 주소 그대로다. since 이후 전부.

    keep 은 제목을 보고 남길지 정하는 함수. 기본은 증시 기사 거름(_is_stock).
    조선 탭은 조선·해운 기사만 남기도록 다른 함수를 넘긴다.
    """
    keep = keep or _is_stock
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
            if kst < since or not keep(title):
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


def _window_days(since: datetime, now: datetime) -> list[str]:
    """수집 창에 걸리는 KST 날짜들. headlines() 가 하루 단위라 쪼개서 부른다.

    01:30 에 돌면 어제와 오늘 이틀이 걸린다. 창이 지나치게 길면(옛 날짜를 손으로
    지정한 경우) 그날 하루만 본다 — 그럴 땐 어차피 RSS 에 옛 기사가 안 남아 있다.
    """
    days, d = [], since
    while d.date() <= now.date() and len(days) < 3:
        days.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    return days or [since.strftime("%Y-%m-%d")]


def _from_google(
    days: list[str], since: datetime, queries: list[str] | None = None, keep=None
) -> list[dict]:
    """구글 뉴스로 빈자리를 메운다. 링크는 중간 페이지를 거친다."""
    keep = keep or _is_stock
    out = []
    for day in days:
        for a in headlines(queries or GOOGLE_QUERIES, day, limit=200):
            title = _clean(a["title"])
            if not keep(title):
                continue
            try:
                at = datetime.strptime(f"{day} {a['time']}", "%Y-%m-%d %H:%M").replace(
                    tzinfo=config.KST
                )
            except ValueError:
                continue
            if at < since:
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


# 2026-06 에 개발자센터(openapi.naver.com)에서 NAVER API HUB 로 옮겨졌다.
# 예전 주소·헤더(X-Naver-Client-*)로 부르면 401(errorCode 024)이다.
NAVER_URL = "https://naverapihub.apigw.ntruss.com/search/v1/news"


def _untag(s: str) -> str:
    """네이버가 검색어에 씌우는 <b> 를 떼고 엔티티를 푼다. <b> 자리에 공백을 넣으면
    '한화오션 ,' 처럼 쉼표 앞에 빈칸이 생긴다."""
    s = re.sub(r"</?b>", "", s)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def _from_naver(
    since: datetime, until: datetime, queries: list[str], keep=None
) -> list[dict] | None:
    """네이버 뉴스 검색. 원문 링크와 앞부분 요약(description)이 같이 온다.

    최신순으로 100건씩 넘기다 since 보다 옛 기사가 나오면 멈춘다(start 는 1000 까지).
    키가 없거나 한 번이라도 실패하면 None — 부르는 쪽이 RSS·구글로 돌아간다.
    매체 이름은 안 주므로 원문 주소의 도메인을 쓴다.
    """
    if not (config.NAVER_CLIENT_ID and config.NAVER_CLIENT_SECRET):
        return None
    keep = keep or _is_stock
    headers = {"X-NCP-APIGW-API-KEY-ID": config.NAVER_CLIENT_ID,
               "X-NCP-APIGW-API-KEY": config.NAVER_CLIENT_SECRET}
    out: list[dict] = []
    try:
        for q in queries:
            for start in range(1, 1000, 100):
                r = requests.get(NAVER_URL, headers=headers, timeout=15, params={
                    "query": q, "display": 100, "start": start, "sort": "date"})
                r.raise_for_status()
                items = r.json().get("items") or []
                for it in items:
                    at = parsedate_to_datetime(it["pubDate"]).astimezone(config.KST)
                    title = _clean(_untag(it["title"]))
                    if not (since <= at <= until) or not keep(title):
                        continue
                    url = it.get("originallink") or it["link"]
                    out.append({
                        "title": title,
                        "source": re.sub(r"^(www|m)\.", "", re.sub(r"^https?://([^/]+).*", r"\1", url)),
                        "time": at.strftime("%m-%d %H:%M"),
                        "at": at,
                        "url": url,
                        "direct": True,
                        "desc": _untag(it.get("description") or ""),
                    })
                if not items or parsedate_to_datetime(items[-1]["pubDate"]) < since:
                    break
    except Exception as exc:
        log.warning("네이버 검색 실패, RSS·구글로 모읍니다: %s", exc)
        return None
    return out


def collect(day: str) -> tuple[list[dict], dict]:
    """day 00:00(KST)부터 지금까지의 기사를 모은다. 직접링크를 앞에 둔다.

    창 끝을 '지금' 으로 두는 이유. 01:30 에 도는데 그 사이 새벽에도 기사가
    올라온다. 잘라내면 가장 최신 기사를 놓친다.

    두 출처가 같은 창을 봐야 한다. 예전엔 구글만 하루로 잘려 있어서, 화면에는
    어제 날짜를 써 놓고 목록은 오늘 기사만 늘어서는 일이 있었다.
    """
    since = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=config.KST)
    return gather(since)


def gather(
    since: datetime, *, queries: list[str] | None = None, keep=None,
    until: datetime | None = None, naver: bool = False,
) -> tuple[list[dict], dict]:
    """since 부터 until(기본 지금)까지 기사를 모아 같은 사건을 하나로 묶는다.

    뉴스 탭(collect)과 조선 탭이 같이 쓴다. 조선 탭은 검색어와 거름 조건만 바꾼다.
    until 은 지난 날짜를 다시 만들 때 쓴다. 안 자르면 오늘 기사가 섞인다.
    naver=True 면 네이버 검색으로 모은다(조선 탭). 못 쓰면 RSS·구글로 돌아간다.
    """
    now = until or datetime.now(config.KST)
    rows = _from_naver(since, now, queries or GOOGLE_QUERIES, keep) if naver else None
    if rows is None:
        rows = _from_feeds(since, keep) + _from_google(
            _window_days(since, now), since, queries, keep
        )
    rows = [r for r in rows if r["at"] <= now]

    # 같은 소식이면 직접링크를 남긴다. 그다음은 이른 기사. dup = 묶인 기사 수.
    # 대표 기사하고만 견준다. 닮은 기사끼리 이어 붙이면 '코스피 약세' 류가 사슬처럼
    # 번져 뉴스 탭 기사 93건이 한 묶음이 됐다(2026-09-29 확인).
    groups: list[tuple[set[str], dict]] = []
    for r in sorted(rows, key=lambda x: (not x["direct"], x["at"])):
        g = _grams(r["title"])
        for lead_g, lead in groups:
            if _alike(g, lead_g):
                lead["dup"] += 1
                break
        else:
            r["dup"] = 1
            groups.append((g, r))

    out = sorted((r for _, r in groups), key=lambda x: x["at"], reverse=True)
    log.info(
        "뉴스 %d건 수집 (직접링크 %d · 구글 %d)",
        len(out), sum(1 for r in out if r["direct"]), sum(1 for r in out if not r["direct"]),
    )
    window = {
        "from": since.strftime("%Y-%m-%d %H:%M"),
        "to": now.strftime("%Y-%m-%d %H:%M"),
    }
    for r in out:
        r.pop("at", None)
    return out, window


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
        got = _ask(_prompt(day, pool), SCHEMA, effort="low")   # 비교 결과는 explain._ask 참고
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
    rows, window = collect(day)
    if not rows:
        log.warning("%s 기사를 하나도 못 모았습니다.", day)
        return None

    summary = summarize(day, rows)
    if not summary:
        return None

    return {
        "date": day,
        "generated_at": datetime.now(config.KST).isoformat(timespec="seconds"),
        "window": window,
        "collected": len(rows),
        "pool": min(len(rows), POOL),
        "headline": summary["headline"],
        "points": summary["points"],
        "top": summary["top"],
    }
