"""Daily video cap for autopilot (file-backed; CEOAgent stub)."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def _state_path() -> Path:
    return Path(os.getenv("JOBS_DIR", str(_REPO / "jobs"))) / ".videos_today"


def video_budget_allows() -> bool:
    try:
        cap = int(os.getenv("MAX_DAILY_VIDEOS", "10"))
    except ValueError:
        cap = 10
    state = _state_path()
    try:
        today = date.today().isoformat()
        if state.is_file():
            raw = state.read_text(encoding="utf-8").strip().split("|")
            if len(raw) == 2 and raw[0] == today:
                count = int(raw[1])
                if count >= cap:
                    return False
    except Exception:
        pass
    return True


def bump_daily_video_count() -> None:
    state = _state_path()
    state.parent.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    count = 0
    if state.is_file():
        raw = state.read_text(encoding="utf-8").strip().split("|")
        if len(raw) == 2 and raw[0] == today:
            count = int(raw[1])
    state.write_text(f"{today}|{count + 1}", encoding="utf-8")


def get_budget_snapshot() -> dict:
    """Today's shipped-to-HITL count vs MAX_DAILY_VIDEOS (file under JOBS_DIR)."""
    try:
        cap = int(os.getenv("MAX_DAILY_VIDEOS", "10"))
    except ValueError:
        cap = 10
    today = date.today().isoformat()
    count = 0
    state = _state_path()
    if state.is_file():
        try:
            raw = state.read_text(encoding="utf-8").strip().split("|")
            if len(raw) == 2 and raw[0] == today:
                count = int(raw[1])
        except Exception:
            pass
    return {
        "date": today,
        "count_today": count,
        "max_daily": cap,
        "allows_more": count < cap,
        "state_file": str(state),
    }
