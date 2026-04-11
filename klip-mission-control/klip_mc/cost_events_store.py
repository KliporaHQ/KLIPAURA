"""
Append-only provider usage / cost events for Mission Control Credits Monitor.

Each line in the JSONL file is one event. Ingestion:
- POST /api/events/ingest with ``event_type == "provider_usage"`` and structured ``data`` (see below), or
- Dedicated credit APIs read from this store only (no billing provider calls).

Environment:
- ``MC_COST_EVENTS_PATH`` — optional absolute path to JSONL file.
  Default: ``<klip-mission-control>/data/cost_events.jsonl``
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Event envelope (one JSON object per line)
# {
#   "id": "uuid",
#   "ts": "2026-04-08T12:00:00+00:00",
#   "module": "klip-avatar",
#   "job_id": "...",
#   "avatar_id": "...",
#   "provider": "wavespeed" | "elevenlabs" | "groq" | "internal",
#   "operation": "i2v" | "tts" | "llm_script" | ...,
#   "amount_usd": 0.0123,
#   "estimate": true,
#   "stage": "optional pipeline stage key",
#   "units": { "seconds": 10, "chars": 500, ... },
#   "source_event_id": "optional upstream ingest id",
# }


def _default_path() -> Path:
    custom = (os.getenv("MC_COST_EVENTS_PATH") or "").strip()
    if custom:
        return Path(custom)
    return Path(__file__).resolve().parent.parent / "data" / "cost_events.jsonl"


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_ts(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def events_file_path() -> Path:
    return _default_path()


def append_event(
    *,
    module: str,
    provider: str,
    operation: str,
    amount_usd: float,
    job_id: str | None = None,
    avatar_id: str | None = None,
    estimate: bool = False,
    stage: str | None = None,
    units: dict[str, Any] | None = None,
    source_event_id: str | None = None,
) -> dict[str, Any]:
    path = _default_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "id": str(uuid.uuid4()),
        "ts": _iso_now(),
        "module": (module or "").strip() or "unknown",
        "provider": (provider or "").strip() or "unknown",
        "operation": (operation or "").strip() or "unknown",
        "amount_usd": float(amount_usd),
        "estimate": bool(estimate),
        "job_id": (job_id or "").strip() or None,
        "avatar_id": (avatar_id or "").strip() or None,
        "stage": (stage or "").strip() or None,
        "units": units if isinstance(units, dict) else {},
        "source_event_id": (source_event_id or "").strip() or None,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def append_from_ingest_payload(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Map Mission Control ingest body to zero or more cost rows.

    Supported:
    - ``event_type == "provider_usage"`` with ``data`` containing provider/operation/amount_usd.
    - ``data["provider_usage"]`` as a list of dicts with the same fields.
    """
    out: list[dict[str, Any]] = []
    et = str(envelope.get("event_type") or "").strip()
    mid = str(envelope.get("module") or "").strip()
    job_id = envelope.get("job_id")
    job_id_s = str(job_id).strip() if job_id is not None else ""
    src_id = str(envelope.get("id") or "").strip() or None
    data = envelope.get("data")
    if not isinstance(data, dict):
        data = {}

    def _one(d: dict[str, Any]) -> None:
        prov = str(d.get("provider") or "").strip()
        op = str(d.get("operation") or "").strip()
        try:
            amt = float(d.get("amount_usd", 0.0))
        except (TypeError, ValueError):
            amt = 0.0
        if not prov and not op and amt == 0.0:
            return
        aid = str(d.get("avatar_id") or data.get("avatar_id") or "").strip() or None
        jid = str(d.get("job_id") or job_id_s or "").strip() or None
        out.append(
            append_event(
                module=mid or str(d.get("module") or "unknown"),
                provider=prov or "unknown",
                operation=op or "usage",
                amount_usd=amt,
                job_id=jid,
                avatar_id=aid,
                estimate=bool(d.get("estimate", data.get("estimate", False))),
                stage=str(d.get("stage") or "").strip() or None,
                units=d.get("units") if isinstance(d.get("units"), dict) else None,
                source_event_id=src_id,
            )
        )

    if et == "provider_usage":
        _one(data)
    batch = data.get("provider_usage")
    if isinstance(batch, list):
        for item in batch:
            if isinstance(item, dict):
                _one(item)
    return out


def read_events(
    *,
    since_hours: float | None = 168,
    job_id: str | None = None,
    avatar_id: str | None = None,
    limit: int = 5000,
) -> list[dict[str, Any]]:
    """Read recent events from tail of file (full scan for typical file sizes)."""
    path = _default_path()
    if not path.is_file():
        return []
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    lines = lines[-max(1, min(100_000, limit * 4)) :]
    cutoff: datetime | None = None
    if since_hours is not None and float(since_hours) > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=float(since_hours))
    jid = (job_id or "").strip().lower() or None
    aid = (avatar_id or "").strip().lower() or None
    out: list[dict[str, Any]] = []
    for line in reversed(lines):
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                continue
            if cutoff:
                ts = _parse_ts(str(row.get("ts") or ""))
                if ts and ts.tzinfo:
                    ts = ts.astimezone(timezone.utc)
                if ts and ts < cutoff.replace(tzinfo=timezone.utc):
                    continue
            if jid:
                rj = str(row.get("job_id") or "").strip().lower()
                if rj != jid:
                    continue
            if aid:
                ra = str(row.get("avatar_id") or "").strip().lower()
                if ra != aid:
                    continue
            out.append(row)
            if len(out) >= limit:
                break
        except json.JSONDecodeError:
            continue
    out.reverse()
    return out


def provider_summary(*, since_hours: float = 168) -> dict[str, Any]:
    rows = read_events(since_hours=since_hours, limit=8000)
    by_provider: dict[str, dict[str, float]] = {}
    total = 0.0
    for r in rows:
        p = str(r.get("provider") or "unknown").strip() or "unknown"
        try:
            amt = float(r.get("amount_usd", 0.0))
        except (TypeError, ValueError):
            amt = 0.0
        total += amt
        slot = by_provider.setdefault(p, {"amount_usd": 0.0, "events": 0.0})
        slot["amount_usd"] += amt
        slot["events"] += 1
    return {
        "since_hours": since_hours,
        "total_usd": round(total, 6),
        "providers": {
            k: {"amount_usd": round(v["amount_usd"], 6), "events": int(v["events"])}
            for k, v in sorted(by_provider.items(), key=lambda x: -x[1]["amount_usd"])
        },
    }


def per_avatar_spend(*, since_hours: float = 168) -> dict[str, Any]:
    rows = read_events(since_hours=since_hours, limit=8000)
    by_av: dict[str, float] = {}
    for r in rows:
        a = str(r.get("avatar_id") or "").strip() or "_unknown"
        try:
            amt = float(r.get("amount_usd", 0.0))
        except (TypeError, ValueError):
            amt = 0.0
        by_av[a] = by_av.get(a, 0.0) + amt
    return {
        "since_hours": since_hours,
        "avatars": {
            k: round(v, 6) for k, v in sorted(by_av.items(), key=lambda x: -x[1])
        },
    }


def job_cost_trace(job_id: str) -> dict[str, Any]:
    jid = (job_id or "").strip()
    rows = read_events(since_hours=None, job_id=jid, limit=500)
    total = sum(float(r.get("amount_usd", 0.0) or 0.0) for r in rows)
    return {"job_id": jid, "events": rows, "total_usd": round(total, 6)}


def cap_status() -> dict[str, Any]:
    """Compare recent spend to optional env caps (no external APIs)."""
    def _fenv(name: str) -> float | None:
        raw = (os.getenv(name) or "").strip()
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    daily_cap = _fenv("CREDITS_DAILY_CAP_USD")
    monthly_cap = _fenv("CREDITS_MONTHLY_CAP_USD")
    rows_24h = read_events(since_hours=24, limit=8000)
    spend_24h = sum(float(r.get("amount_usd", 0.0) or 0.0) for r in rows_24h)
    rows_720 = read_events(since_hours=720, limit=20000)
    spend_30d = sum(float(r.get("amount_usd", 0.0) or 0.0) for r in rows_720)

    burn_per_hour = round(spend_24h / 24.0, 8) if spend_24h else 0.0

    out = {
        "last_24h_spend_usd": round(spend_24h, 6),
        "last_30d_spend_usd": round(spend_30d, 6),
        "burn_rate_usd_per_hour": burn_per_hour,
        "daily_cap_usd": daily_cap,
        "monthly_cap_usd": monthly_cap,
        "daily_remaining_usd": round(daily_cap - spend_24h, 6) if daily_cap is not None else None,
        "daily_cap_breach": bool(daily_cap is not None and spend_24h > daily_cap),
        "monthly_cap_breach": bool(monthly_cap is not None and spend_30d > monthly_cap),
        "wavespeed_max_i2v_per_hour": _fenv("WAVESPEED_MAX_I2V_PER_HOUR"),
        "notes": [],
    }
    if daily_cap is None and monthly_cap is None:
        out["notes"].append("Set CREDITS_DAILY_CAP_USD / CREDITS_MONTHLY_CAP_USD to enable cap alerts.")
    return out


__all__ = [
    "append_event",
    "append_from_ingest_payload",
    "cap_status",
    "events_file_path",
    "job_cost_trace",
    "per_avatar_spend",
    "provider_summary",
    "read_events",
]
