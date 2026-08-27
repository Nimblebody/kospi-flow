#!/usr/bin/env python3
"""
코스피 테마·수급 리포트 생성기.

    python main.py --stage flash          # 장 마감 직후 속보
    python main.py --stage final          # 수급 확정 후
    python main.py --sample               # KIS 키 없이 가짜 데이터로 화면만 확인
    python main.py --backfill 14          # 과거 14영업일치를 채워 자금 이동 활성화

결과물
    web/data/<YYYY-MM-DD>.json  : 그날 리포트
    web/data/latest.json        : 최신 리포트 (대시보드가 읽는 파일)
    raw/<날짜>_<stage>.json     : 원본 스냅샷 (재분석용)
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime

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


def backfill(end_date: str, days: int) -> None:
    """과거 영업일 리포트를 확정 수급으로 채운다.

    자금 이동과 연속일수는 전 거래일 리포트가 있어야 계산된다. 종목당 1콜에
    30일치가 오므로 2주든 한 달이든 콜 수는 같다.
    """
    from src import history
    from src.collect import collect_market
    from src.kis import KisClient

    dates = history.business_days(end_date, days)
    log.info("백필 %s ~ %s (%d영업일)", dates[0], dates[-1], len(dates))

    kis = KisClient()
    themes, theme_source = masters.load_themes()
    stock_themes = masters.build_stock_to_themes(themes)
    stock_sectors = masters.load_stock_sectors()

    codes = history.universe()
    # 실측 752종목 3분 15초 (동시 8스레드). 왕복 지연 때문에 초당 8콜까지는 안 나온다.
    log.info("확정 수급 수집 %d종목 (종목당 1콜, 약 %.0f분)", len(codes), len(codes) / 4 / 60)
    series = history.collect_series(kis, codes, end_date, set(dates))

    made = 0
    for date in dates:  # 오래된 날부터. 전날 리포트가 있어야 자금 이동이 나온다
        flows = series.get(date) or {}
        if not flows:
            log.warning("%s 수급 없음 (휴장일로 보임). 건너뜁니다.", date)
            continue

        # 지수·업종은 과거를 되돌려주는 API 가 없다. 오늘 날짜만 실시간으로 채운다.
        market = collect_market(kis) if date == end_date else None
        snapshot = history.make_snapshot(date, flows, market, codes)
        iso = f"{date[:4]}-{date[4:6]}-{date[6:]}"
        snapshot["date"] = iso

        # 달력상 전 영업일이 아니라 '실제로 있는 가장 최근 리포트' 를 쓴다.
        # 휴장일(대체공휴일 등)에는 리포트가 없어 자금 이동이 통째로 빠진다.
        hist = store.load_history(iso, limit=10)

        report = analyzer.analyze(
            snapshot,
            themes,
            stock_themes,
            previous_themes=hist[0].get("themes") if hist else None,
            history_themes=[h.get("themes", []) for h in hist],
            theme_source=theme_source + " (확정 수급)",
            stock_sectors=stock_sectors,
        )
        # 마지막 날만 latest.json 을 갱신한다
        store.save_report(report, make_latest=(date == dates[-1]))
        made += 1
        rot = report["rotation"]
        log.info(
            "  %s 저장 · 테마 %d개 · 자금이동 %s",
            iso, len(report["themes"]), "있음" if rot["available"] else "없음",
        )

    render.build_site()
    log.info("백필 완료: %d일치", made)


def run(stage: str, date: str, *, use_sample: bool, do_notify: bool) -> dict:
    themes, theme_source = masters.load_themes()
    stock_themes = masters.build_stock_to_themes(themes)
    stock_sectors = masters.load_stock_sectors()

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

    history = store.load_history(iso_date, limit=10)

    report = analyzer.analyze(
        snapshot,
        themes,
        stock_themes,
        previous_themes=history[0].get("themes") if history else None,
        history_themes=[h.get("themes", []) for h in history],
        theme_source=theme_source,
        stock_sectors=stock_sectors,
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
    p.add_argument(
        "--backfill",
        type=int,
        metavar="N",
        help="과거 N영업일 리포트를 확정 수급으로 채운다 (자금 이동 표시용)",
    )
    args = p.parse_args()

    if args.backfill:
        backfill(args.date or _default_date(), args.backfill)
        return

    run(
        args.stage,
        args.date or _default_date(),
        use_sample=args.sample,
        do_notify=not args.no_notify,
    )


if __name__ == "__main__":
    main()
