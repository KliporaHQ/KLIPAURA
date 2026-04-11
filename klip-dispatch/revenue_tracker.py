"""Append-only affiliate ledger at repo root: revenue.jsonl"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
LEDGER = Path(os.getenv("REVENUE_LEDGER_PATH", str(_REPO / "revenue.jsonl"))).resolve()


def log(
    job_id: str,
    avatar_id: str,
    product_url: str,
    *,
    platform: str = "tiktok",
    asin: str = "",
    commission_rate: float = 0.0,
    est_revenue_usd: float = 0.0,
    publish_status: str = "logged",
    post_url: str | None = None,
    detail: dict | None = None,
) -> None:
    entry = {
        "job_id": job_id,
        "avatar_id": avatar_id,
        "platform": platform,
        "product_url": product_url,
        "asin": asin,
        "commission_rate": commission_rate,
        "est_revenue_usd": est_revenue_usd,
        "publish_status": publish_status,
        "post_url": post_url,
        "detail": detail or {},
        "logged_at": datetime.now(timezone.utc).isoformat(),
    }
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    try:
        _maybe_notify_spawn(avatar_id)
    except Exception:
        pass


def _spawn_flag_path(avatar_id: str, threshold_usd: int) -> Path:
    jd = Path(os.getenv("JOBS_DIR", str(_REPO / "jobs")))
    jd.mkdir(parents=True, exist_ok=True)
    return jd / f".spawn_{threshold_usd}usd_notified_{avatar_id}"


def _maybe_notify_spawn(avatar_id: str) -> None:
    th = int(float(os.getenv("SPAWN_MILESTONE_USD", "1000")))
    s = get_summary(avatar_id=avatar_id)
    total = float(s.get("est_revenue_usd_sum") or 0)
    if total < th:
        return
    flag = _spawn_flag_path(avatar_id, th)
    if flag.is_file():
        return
    from infrastructure.telegram_notify import send_telegram

    msg = (
        f"KLIPAURA spawn gate: avatar `{avatar_id}` reached est. ${total:.2f} "
        f"(threshold ${th}). See TASK_TRACKER.md — next avatar prep only after your review."
    )
    if send_telegram(msg):
        flag.write_text(json.dumps({"ts": "notified"}, ensure_ascii=False), encoding="utf-8")


def get_summary(avatar_id: str | None = None) -> dict:
    if not LEDGER.is_file():
        return {"total_entries": 0, "est_revenue_usd_sum": 0.0, "avatars": []}
    entries = []
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if avatar_id:
        entries = [e for e in entries if e.get("avatar_id") == avatar_id]
    total_est = sum(float(e.get("est_revenue_usd") or 0) for e in entries)
    avatars = sorted({e.get("avatar_id") for e in entries if e.get("avatar_id")})
    return {
        "total_entries": len(entries),
        "est_revenue_usd_sum": total_est,
        "avatars": avatars,
    }


def check_spawn_milestone(threshold_usd: float = 1000.0, avatar_id: str | None = None) -> bool:
    """True when ledger `est_revenue_usd` sum for avatar (or all) meets threshold."""
    s = get_summary(avatar_id=avatar_id)
    return float(s.get("est_revenue_usd_sum") or 0) >= float(threshold_usd)
