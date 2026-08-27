"""텔레그램 알림."""
from __future__ import annotations

import html
import logging

import requests

import config

log = logging.getLogger(__name__)


def _fmt(v: float) -> str:
    sign = "+" if v > 0 else "−" if v < 0 else ""
    a = abs(v)
    if a >= 10000:
        return f"{sign}{a / 10000:.2f}조"
    return f"{sign}{a:,.0f}억"


def build_message(report: dict) -> str:
    stage_label = "속보" if report["stage"] == "flash" else "확정"
    lines = [f"<b>📊 코스피 수급 리포트 · {report['date']} ({stage_label})</b>", ""]

    for h in report["headline"]:
        lines.append(html.escape(h))
    lines.append("")

    tops = report["themes_top"][:5]
    if tops:
        lines.append("<b>💰 자금 유입 테마</b>")
        for i, t in enumerate(tops, 1):
            streak = t.get("streak", 0)
            tag = f" ({streak}일 연속)" if streak >= 2 else ""
            lines.append(
                f"{i}. {html.escape(t['name'])} {_fmt(t['net_eok'])}"
                f" · 평균 {t['avg_chg_pct']:+.2f}%{tag}"
            )
        lines.append("")

    bots = report["themes_bottom"][:3]
    if bots:
        lines.append("<b>📉 자금 이탈 테마</b>")
        for t in bots:
            lines.append(f"· {html.escape(t['name'])} {_fmt(t['net_eok'])}")
        lines.append("")

    pairs = report["rotation"].get("pairs") or []
    if pairs:
        lines.append("<b>🔁 테마 간 이동 (추정)</b>")
        for p in pairs[:3]:
            lines.append(
                f"· {html.escape(p['from'])} → {html.escape(p['to'])} "
                f"{_fmt(p['amount_eok'])}"
            )
        lines.append("")

    if config.SITE_URL:
        lines.append(f'<a href="{config.SITE_URL}">전체 리포트 열기 →</a>')

    return "\n".join(lines)


def _ready() -> bool:
    if config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_IDS:
        return True
    log.info("텔레그램 설정이 없어 알림을 건너뜁니다.")
    return False


def send_document(path, caption: str = "") -> bool:
    """리포트 HTML 파일 자체를 텔레그램으로 보낸다.

    사이트를 따로 호스팅하지 않을 때 쓰는 경로. 받은 파일을 탭하면
    텔레그램 내장 브라우저에서 대시보드가 그대로 열린다.
    """
    if not _ready():
        return False

    sent = 0
    for chat_id in config.TELEGRAM_CHAT_IDS:
        try:
            # 받는 사람마다 파일을 새로 읽는다. 앞사람에게 보내면서 커서가 끝까지 가서,
            # 같은 핸들을 다시 쓰면 두 번째부터 빈 파일이 올라간다.
            with open(path, "rb") as fh:
                res = requests.post(
                    f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendDocument",
                    data={
                        "chat_id": chat_id,
                        "caption": caption[:1000],
                        "parse_mode": "HTML",
                    },
                    files={"document": (path.name, fh, "text/html")},
                    timeout=60,
                )
            res.raise_for_status()
            sent += 1
        except Exception as exc:
            log.warning("텔레그램 파일 전송 실패 (%s): %s", chat_id, exc)

    if sent:
        log.info("텔레그램 리포트 파일 전송 완료 (%d/%d)", sent, len(config.TELEGRAM_CHAT_IDS))
    return sent > 0


def send(report: dict) -> bool:
    """받는 사람이 여럿이면 각각 보낸다. 한 명이 실패해도 나머지는 간다."""
    if not _ready():
        return False

    text = build_message(report)
    sent = 0
    for chat_id in config.TELEGRAM_CHAT_IDS:
        try:
            res = requests.post(
                f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=20,
            )
            res.raise_for_status()
            sent += 1
        except Exception as exc:
            # 한 명이 봇을 차단했다고 다른 사람 알림까지 막을 이유는 없다.
            log.warning("텔레그램 전송 실패 (%s): %s", chat_id, exc)

    if sent:
        log.info("텔레그램 알림 전송 완료 (%d/%d)", sent, len(config.TELEGRAM_CHAT_IDS))
    return sent > 0
