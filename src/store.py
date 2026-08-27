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
_THEME_KEEP = ("name", "net_eok", "foreign_eok", "institution_eok", "streak")

# 날짜별 파일에 남길 최상위 키. themes 는 아래에서 따로 추린다.
_TOP_KEEP = (
    "date", "stage", "collected_at", "theme_source",
    "headline", "investors", "indices", "rotation",
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


def save_report(report: dict, *, make_latest: bool = True) -> Path:
    """날짜별 파일은 얇게, latest.json 은 화면이 쓰는 전체를 저장한다.

    make_latest=False 는 백필용. 과거 리포트가 latest.json 을 덮으면 안 된다.
    """
    path = report_path(report["date"])
    path.write_text(_dump(slim(report)), encoding="utf-8")

    if make_latest:
        (config.DATA_DIR / "latest.json").write_text(_dump(report), encoding="utf-8")

    dates = sorted(
        (p.stem for p in config.DATA_DIR.glob("20*.json")), reverse=True
    )
    (config.DATA_DIR / "index.json").write_text(
        json.dumps({"dates": dates[:180]}, ensure_ascii=False), encoding="utf-8"
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
