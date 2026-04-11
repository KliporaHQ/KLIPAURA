"""
Per-avatar video analytics writer.

Appends a record to ``data/avatars/{avatar_id}/analytics/video_history.json``
after every successful render. Also maintains running totals in
``data/avatars/{avatar_id}/analytics/performance.json``.

All writes are non-blocking and fail silently — never allowed to break the
render pipeline.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

# Root of the KLIP-AVATAR repo (3 levels up from this file)
def _avatar_data_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "..", "..", "data", "avatars"))


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path: str, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def record_video_render(
    avatar_id: str,
    job_id: str,
    *,
    video_path: Optional[str] = None,
    video_url: Optional[str] = None,
    render_mode: str = "ken_burns",
    duration_seconds: float = 0.0,
    processing_time_seconds: float = 0.0,
    status: str = "success",
    topic: str = "",
    platform: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Append one render record to the avatar's video_history.json.
    Returns True on success, False on any error (never raises).
    """
    if not avatar_id or not job_id:
        return False
    try:
        analytics_dir = os.path.join(_avatar_data_root(), avatar_id, "analytics")
        if not os.path.isdir(analytics_dir):
            os.makedirs(analytics_dir, exist_ok=True)

        history_path = os.path.join(analytics_dir, "video_history.json")
        history = _load_json(history_path, {"avatar_id": avatar_id, "last_updated": _iso_now(), "videos": []})
        if not isinstance(history.get("videos"), list):
            history["videos"] = []

        record: Dict[str, Any] = {
            "job_id": job_id,
            "render_mode": render_mode,
            "status": status,
            "topic": topic or "",
            "platform": platform or "",
            "duration_seconds": round(duration_seconds, 2),
            "processing_time_seconds": round(processing_time_seconds, 2),
            "rendered_at": _iso_now(),
        }
        if video_url:
            record["video_url"] = video_url
        if video_path:
            record["video_path"] = video_path
        if extra:
            record.update({k: v for k, v in extra.items() if k not in record})

        history["videos"].append(record)
        # Keep the last 500 entries
        history["videos"] = history["videos"][-500:]
        history["last_updated"] = _iso_now()
        _save_json(history_path, history)

        # Update running totals in performance.json
        _update_performance(analytics_dir, avatar_id, render_mode, status, duration_seconds)

        log.debug(
            "[AVATAR ANALYTICS] recorded render job=%s avatar=%s mode=%s status=%s",
            job_id, avatar_id, render_mode, status,
        )
        return True
    except Exception as exc:
        log.debug("[AVATAR ANALYTICS] write failed (non-fatal): %s", exc)
        return False


def _update_performance(
    analytics_dir: str,
    avatar_id: str,
    render_mode: str,
    status: str,
    duration_seconds: float,
) -> None:
    """Update aggregate counters in performance.json (best-effort)."""
    perf_path = os.path.join(analytics_dir, "performance.json")
    perf = _load_json(perf_path, {
        "avatar_id": avatar_id,
        "total_renders": 0,
        "successful_renders": 0,
        "fallback_renders": 0,
        "total_duration_seconds": 0.0,
        "render_modes": {},
        "last_updated": _iso_now(),
    })

    perf["total_renders"] = int(perf.get("total_renders") or 0) + 1
    if status == "success":
        perf["successful_renders"] = int(perf.get("successful_renders") or 0) + 1
    elif status in ("fallback", "fallback_ken_burns"):
        perf["fallback_renders"] = int(perf.get("fallback_renders") or 0) + 1
    perf["total_duration_seconds"] = float(perf.get("total_duration_seconds") or 0.0) + duration_seconds

    modes = perf.get("render_modes") if isinstance(perf.get("render_modes"), dict) else {}
    modes[render_mode] = int(modes.get(render_mode) or 0) + 1
    perf["render_modes"] = modes
    perf["last_updated"] = _iso_now()

    _save_json(perf_path, perf)
