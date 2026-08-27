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


def _mask(chat_id: str) -> str:
    """공개 저장소 로그에 그대로 남기지 않는다. 맞는지 알아볼 만큼만 보인다."""
    if len(chat_id) <= 4:
        return "*" * len(chat_id)
    return chat_id[:3] + "*" * (len(chat_id) - 5) + chat_id[-2:]


def selftest() -> bool:
    """토큰과 챗 아이디를 실제로 두드려 보고 무엇이 막혔는지 말한다.

    python main.py --tg-test
    값은 찍지 않는다. 토큰은 길이만, 챗 아이디는 앞뒤 몇 자만 보여준다.
    """
    token = config.TELEGRAM_BOT_TOKEN
    ids = config.TELEGRAM_CHAT_IDS

    print(f"TELEGRAM_BOT_TOKEN : {f'설정됨 ({len(token)}자)' if token else '비어 있음'}")
    print(f"TELEGRAM_CHAT_ID   : {len(ids)}개 {[_mask(i) for i in ids] or ''}")
    print("-" * 58)

    if not token:
        print("토큰이 비어 있습니다.")
        print("  Settings > Secrets and variables > Actions > Repository secrets 에")
        print("  TELEGRAM_BOT_TOKEN 이름으로 넣었는지 확인하세요.")
        print("  (Variables 탭이나 Dependabot 탭에 넣으면 워크플로에서 안 보입니다)")
        return False

    try:
        body = requests.get(
            f"https://api.telegram.org/bot{token}/getMe", timeout=15
        ).json()
    except Exception as exc:
        print(f"텔레그램에 연결하지 못했습니다: {exc}")
        return False

    if not body.get("ok"):
        print(f"토큰이 잘못됐습니다: {body.get('error_code')} {body.get('description')}")
        print("  @BotFather 의 /mybots 에서 토큰을 다시 확인하세요.")
        return False

    me = body.get("result") or {}
    print(f"봇 확인 : @{me.get('username')} ({me.get('first_name')})")

    if not ids:
        print("챗 아이디가 비어 있습니다. TELEGRAM_CHAT_ID 를 채우세요.")
        print(f"  받는 사람이 @{me.get('username')} 와 대화를 시작한 뒤")
        print(f"  https://api.telegram.org/bot<토큰>/getUpdates 에서 chat.id 를 봅니다.")
        return False

    print("-" * 58)
    ok = 0
    for chat in ids:
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id": chat,
                    "text": "✅ 코스피 수급 지도 · 알림 설정 확인용 메시지입니다.",
                    "disable_web_page_preview": True,
                },
                timeout=20,
            ).json()
        except Exception as exc:
            print(f"  {_mask(chat)} : 연결 실패 — {exc}")
            continue

        if r.get("ok"):
            print(f"  {_mask(chat)} : 보냈습니다 ✓")
            ok += 1
            continue

        code, desc = r.get("error_code"), (r.get("description") or "")
        print(f"  {_mask(chat)} : 실패 — {code} {desc}")
        if "chat not found" in desc:
            print("      아이디가 틀렸거나, 그 사람이 아직 봇과 대화를 시작하지 않았습니다.")
            print(f"      https://t.me/{me.get('username')} 에서 /start 를 누르게 하세요.")
        elif "blocked" in desc:
            print("      그 사람이 봇을 차단했습니다.")
        elif "can't initiate conversation" in desc:
            print(f"      봇이 먼저 말을 걸 수 없습니다. https://t.me/{me.get('username')} 에서 /start.")
        elif "group chat was upgraded" in desc:
            print("      그룹이 슈퍼그룹으로 바뀌었습니다. -100 으로 시작하는 새 아이디를 쓰세요.")

    print("-" * 58)
    print(f"결과: {ok}/{len(ids)} 성공")
    return ok > 0


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
            if "404" in str(exc):
                # 404 는 받는 사람이 아니라 주소(=토큰)를 못 찾았다는 뜻이다.
                log.warning(
                    "  404 는 챗 아이디가 아니라 봇 토큰이 잘못됐다는 뜻입니다. "
                    "python main.py --tg-test 로 확인하세요."
                )

    if sent:
        log.info("텔레그램 알림 전송 완료 (%d/%d)", sent, len(config.TELEGRAM_CHAT_IDS))
    return sent > 0
