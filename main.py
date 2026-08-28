#!/usr/bin/env python3
"""
코스피 테마·수급 리포트 생성기.

    python main.py --stage flash          # 장 마감 직후 속보
    python main.py --stage final          # 수급 확정 후
    python main.py --sample               # KIS 키 없이 가짜 데이터로 화면만 확인
    python main.py --backfill 14          # 과거 14영업일치를 채워 자금 이동 활성화
    python main.py --build-only           # API 없이 web/ 정적 파일만 다시 빌드
    python main.py --probe-us             # 미국 시세 심볼 코드가 뭐가 통하는지 확인
    python main.py --tg-test              # 텔레그램 알림 설정 점검 (확인 메시지 1통)

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


def data_not_ready_yet(date: str, now: datetime | None = None) -> bool:
    """그날 수급이 아직 안 나온 게 정상인 시점인가.

    수급이 비어 있을 때 이게 True 면 실패가 아니라 '아직'이다.
    GitHub 크론은 제때 발사된다는 보장이 없다. 09:30 UTC 예약이 19:53 UTC 에
    돈 적이 있는데(KST 04:53), 그 시각에 당일 수급이 있을 리 없다.
    """
    now = now or datetime.now(config.KST)
    today = now.strftime("%Y%m%d")
    if date > today:          # 미래 날짜는 당연히 없다
        return True
    if date < today:          # 지난 거래일인데 비었으면 그건 문제다
        return False
    return (now.hour, now.minute) < config.DATA_READY_KST


def too_early_to_collect(date: str, now: datetime | None = None) -> bool:
    """지금 긁어봐야 그날 수급이 있을 수 없는가.

    752종목에 1콜씩 3분을 쓰고 나서 비었다는 걸 아는 건 낭비다.
    아직 오지 않은 날이거나 장이 안 끝났으면 수집 자체를 건너뛴다.
    """
    now = now or datetime.now(config.KST)
    today = now.strftime("%Y%m%d")
    if date > today:          # 아직 오지 않은 날
        return True
    if date < today:          # 이미 끝난 장
        return False
    return (now.hour, now.minute) < config.MARKET_CLOSE_KST


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

        # 수급이 있을 수 없는 시점이면 752종목을 긁기 전에 끝낸다.
        if too_early_to_collect(date):
            log.warning(
                "%s 수급이 나올 시점이 아닙니다(코스피 마감 KST %02d:%02d). "
                "수집하지 않고 종료합니다. "
                "지난 거래일을 만들려면 --date 로 날짜를 주세요.",
                date, *config.MARKET_CLOSE_KST,
            )
            sys.exit(0)

        names = masters.load_stock_names()
        from src.collect import collect_all

        snapshot = collect_all(kis, stage=stage, date=date)
        for row in snapshot["flows"]:
            if not row["name"]:
                row["name"] = names.get(row["code"], row["code"])

        # 국내가 비었으면 여기서 멈춘다. 미국까지 1분 더 긁고 나서 죽을 이유가 없다.
        if not snapshot["flows"]:
            if data_not_ready_yet(date):
                # 아직 안 나온 것뿐이다. 빨간 X 를 띄울 일이 아니다.
                log.warning(
                    "%s 수급이 아직 없습니다. 리포트를 만들지 않고 정상 종료합니다. "
                    "코스피는 15:30 에 끝나고 확정 수급은 그 뒤에 올라옵니다. "
                    "KST %02d:%02d 이전 실행에서는 비어 있는 게 정상입니다.",
                    date, *config.DATA_READY_KST,
                )
                sys.exit(0)

            log.error(
                "%s 수급 데이터가 비어 있습니다. 리포트를 만들지 않습니다.\n"
                "  장이 끝난 시각인데도 비었습니다. 휴장일이거나 KIS 응답에 문제가 있을 수 있습니다. "
                "휴장일 조회(chk-holiday)가 막히면 여기까지 옵니다.\n"
                "  지난 거래일을 다시 만들려면 --date 20260827 처럼 날짜를 주세요 "
                "(Actions 에서는 Run workflow 의 date 칸).",
                date,
            )
            sys.exit(1)

        # 미국증시는 곁들이는 자리다. 여기서 넘어져도 국내 리포트는 그대로 나간다.
        try:
            from src import overseas

            snapshot["us"] = overseas.collect_us(kis)
        except Exception as exc:
            log.warning("미국증시 수집 실패(무시): %s", exc)

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
        "--tg-test",
        action="store_true",
        help="텔레그램 토큰·챗 아이디를 두드려 보고 확인 메시지를 한 통 보낸다",
    )
    p.add_argument(
        "--probe-us",
        action="store_true",
        help="미국 시세 후보 심볼을 전부 넣어 보고 되는 것을 표로 찍는다",
    )
    p.add_argument(
        "--build-only",
        action="store_true",
        help="KIS 호출 없이 web/ 정적 파일만 다시 만든다 (화면만 고쳤을 때)",
    )
    p.add_argument(
        "--backfill",
        type=int,
        metavar="N",
        help="과거 N영업일 리포트를 확정 수급으로 채운다 (자금 이동 표시용)",
    )
    args = p.parse_args()

    if args.tg_test:
        sys.exit(0 if notify.selftest() else 1)

    if args.probe_us:
        from src.kis import KisClient
        from src import overseas

        overseas.probe(KisClient())
        return

    # 화면만 고친 경우. 데이터는 web/data 에 이미 있으니 API 를 부를 이유가 없다.
    if args.build_only:
        render.build_site()
        return

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
