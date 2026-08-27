"""리포트 저장 / 과거 리포트 로딩."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import config

log = logging.getLogger(__name__)


def report_path(date: str) -> Path:
    return config.DATA_DIR / f"{date}.json"


def save_report(report: dict) -> Path:
    path = report_path(report["date"])
    path.write_text(
        json.dumps(report, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )

    latest = config.DATA_DIR / "latest.json"
    latest.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")

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
