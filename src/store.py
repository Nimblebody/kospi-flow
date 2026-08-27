"""리포트 저장 / 과거 리포트 로딩."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import config

log = logging.getLogger(__name__)


def report_path(date: str) -> Path:
    return config.DATA_DIR / f"{date}.json"


# 날짜별 파일에 남길 테마 필드. 자금 흐름 시계열에 필요한 것만.
# featured 를 남기는 이유 — 축약본만 있는 과거 날짜에서도 화면이 테마 순위를
# 다시 뽑아야 하는데, 이게 없으면 대형주 하나가 끌고 가는 테마가 상위에 올라와
# 최신 화면과 순위가 달라진다.
_THEME_KEEP = (
    "name", "net_eok", "foreign_eok", "institution_eok", "streak", "featured",
)

# 날짜별 파일에 남길 최상위 키. themes 는 아래에서 따로 추린다.
_TOP_KEEP = (
    "date", "stage", "collected_at", "theme_source",
    "headline", "investors", "indices", "rotation", "us",
)


def slim(report: dict) -> dict:
    """날짜별 보관용으로 줄인다.

    대시보드는 latest.json 만 읽고, 날짜별 파일은 자금 이동·연속일수 계산에
    테마 이름과 금액만 쓰인다. 테마마다 상위 5종목을 중복 저장하는 stocks 가
    파일의 절반이라, 이것만 빼도 190KB 가 23KB 가 된다.

    되돌릴 수 없는 선택이다. KIS 는 30일치만 돌려주므로, 그 이후에는
    종목별 상세를 다시 만들 방법이 없다.
    """
    out = {k: report[k] for k in _TOP_KEEP if k in report}
    out["themes"] = [
        {k: t[k] for k in _THEME_KEEP if k in t} for t in report.get("themes", [])
    ]
    return out


def _dump(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def is_full(report: dict) -> bool:
    """얇게 줄인 보관본이 아니라 화면을 다 그릴 수 있는 전체본인가."""
    return "themes_top" in report


def apply_retention() -> list[str]:
    """보관 기간이 지난 리포트를 얇게 줄이고, 전체본이 남은 날짜를 돌려준다.

    이미 커밋된 파일을 나중에 줄여도 .git 은 작아지지 않는다(예전 버전이 남는다).
    용량이 아니라 '화면에서 되돌아가 볼 수 있는 범위' 를 정하는 일이다.
    """
    dates = sorted((p.stem for p in config.DATA_DIR.glob("20*.json")), reverse=True)
    full: list[str] = []
    for i, d in enumerate(dates):
        path = report_path(d)
        try:
            rep = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not is_full(rep):
            continue
        if i < config.FULL_REPORT_DAYS:
            full.append(d)
        else:
            path.write_text(_dump(slim(rep)), encoding="utf-8")
            log.info("보관 기간 경과로 축약: %s", d)
    return full


def save_report(report: dict, *, make_latest: bool = True) -> Path:
    """전체본으로 저장하고, 보관 기간이 지난 과거분만 얇게 줄인다.

    make_latest=False 는 백필용. 과거 리포트가 latest.json 을 덮으면 안 된다.
    """
    path = report_path(report["date"])
    path.write_text(_dump(report), encoding="utf-8")

    if make_latest:
        (config.DATA_DIR / "latest.json").write_text(_dump(report), encoding="utf-8")

    full = apply_retention()
    dates = sorted((p.stem for p in config.DATA_DIR.glob("20*.json")), reverse=True)
    (config.DATA_DIR / "index.json").write_text(
        json.dumps(
            # full: 그날 화면을 통째로 다시 그릴 수 있는 날짜. 나머지는 요약만 있다.
            {"dates": dates[:180], "full": sorted(full, reverse=True)},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    log.info("리포트 저장: %s", path)
    return path


def save_raw(snapshot: dict) -> Path:
    """원본 스냅샷은 저장소 안 raw/ 에 따로 둔다 (재분석용, 웹에는 안 올림)."""
    raw_dir = config.ROOT / "raw"
    raw_dir.mkdir(exist_ok=True)
    path = raw_dir / f"{snapshot['date']}_{snapshot['stage']}.json"
    path.write_text(
        json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    return path


def load_report(date: str) -> dict | None:
    path = report_path(date)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def load_history(before: str, limit: int = 10) -> list[dict]:
    """`before` 날짜 이전 리포트를 최신순으로 최대 limit 개."""
    dates = sorted(
        (p.stem for p in config.DATA_DIR.glob("20*.json") if p.stem < before),
        reverse=True,
    )[:limit]
    out = []
    for d in dates:
        r = load_report(d)
        if r:
            out.append(r)
    return out
