#!/usr/bin/env python3
"""
코스피 테마·수급 리포트 생성기.

    python main.py --stage flash          # 장 마감 직후 속보
    python main.py --stage final          # 수급 확정 후
    python main.py --sample               # KIS 키 없이 가짜 데이터로 화면만 확인

결과물
    web/data/<YYYY-MM-DD>.json  : 그날 리포트
    web/data/latest.json        : 최신 리포트 (대시보드가 읽는 파일)
    raw/<날짜>_<stage>.json     : 원본 스냅샷 (재분석용)
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta

import config
from src import analyze as analyzer
from src import masters, notify, render, store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("main")


def _default_date() -> str:
    return datetime.now(config.KST).strftime("%Y%m%d")


def _prev_business_day(date: str) -> str:
    d = datetime.strptime(date, "%Y%m%d")
    d -= timedelta(days=1)
    while d.weekday() >= 5:  # 토/일
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


def run(stage: str, date: str, *, use_sample: bool, do_notify: bool) -> dict:
    themes, theme_source = masters.load_themes()
    stock_themes = masters.build_stock_to_themes(themes)

    if use_sample:
        from make_sample import make_snapshot

        snapshot = make_snapshot(date, stage, themes)
        theme_source += " (샘플 데이터)"
    else:
        from src.kis import KisClient

        kis = KisClient()
        if kis.is_holiday(date) is True:
            log.info("%s 은 휴장일입니다. 종료합니다.", date)
            sys.exit(0)

        names = masters.load_stock_names()
        from src.collect import collect_all

        snapshot = collect_all(kis, stage=stage, date=date)
        for row in snapshot["flows"]:
            if not row["name"]:
                row["name"] = names.get(row["code"], row["code"])

    if not snapshot["flows"]:
        log.error("수급 데이터가 비어 있습니다. 리포트를 만들지 않습니다.")
        sys.exit(1)

    store.save_raw(snapshot)

    iso_date = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    snapshot["date"] = iso_date

    prev_iso = _prev_business_day(date)
    prev_iso = f"{prev_iso[:4]}-{prev_iso[4:6]}-{prev_iso[6:]}"
    prev_report = store.load_report(prev_iso)
    history = store.load_history(iso_date, limit=10)

    report = analyzer.analyze(
        snapshot,
        themes,
        stock_themes,
        previous_themes=(prev_report or {}).get("themes"),
        history_themes=[h.get("themes", []) for h in history],
        theme_source=theme_source,
    )

    store.save_report(report)
    render.build_site()

    for line in report["headline"]:
        log.info("  %s", line)

    if do_notify:
        notify.send(report)
        # 사이트 주소가 없으면(호스팅을 안 쓰면) 리포트 HTML 파일 자체를 보낸다
        if not config.SITE_URL:
            from pathlib import Path

            single = Path(config.ROOT / f"report_{iso_date}.html")
            render.build_standalone(report, single)
            notify.send_document(single, f"{iso_date} 리포트 · 파일을 눌러 열어보세요")
            single.unlink(missing_ok=True)

    return report


def main() -> None:
    p = argparse.ArgumentParser(description="코스피 테마·수급 리포트")
    p.add_argument(
        "--stage",
        choices=["flash", "final"],
        default="final",
        help="flash=장 마감 직후 속보, final=수급 확정 후",
    )
    p.add_argument("--date", default=None, help="YYYYMMDD (기본값: 오늘, KST)")
    p.add_argument(
        "--sample", action="store_true", help="KIS 없이 샘플 데이터로 실행 (화면 확인용)"
    )
    p.add_argument("--no-notify", action="store_true", help="텔레그램 알림 끄기")
    args = p.parse_args()

    run(
        args.stage,
        args.date or _default_date(),
        use_sample=args.sample,
        do_notify=not args.no_notify,
    )


if __name__ == "__main__":
    main()
