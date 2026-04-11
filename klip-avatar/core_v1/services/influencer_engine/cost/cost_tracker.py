"""
Influencer Engine — cost tracker.

Tracks LLM, TTS, video generation cost per job and globally.
Uses in-memory store; optional Redis for persistence.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# In-memory: job_id -> list of cost entries; global totals
_job_costs: Dict[str, List[Dict[str, Any]]] = {}
_global_llm: float = 0.0
_global_tts: float = 0.0
_global_video: float = 0.0


def _redis():
    try:
        from klipaura_core.infrastructure.redis_client import get_redis_client
        return get_redis_client()
    except Exception:
        return None


def _record(
    job_id: str,
    category: str,
    amount: float,
    units: str = "",
    metadata: Optional[Dict[str, Any]] = None,
    avatar_id: Optional[str] = None,
) -> None:
    global _global_llm, _global_tts, _global_video
    meta = dict(metadata or {})
    if avatar_id:
        meta["avatar_id"] = avatar_id
    entry = {
        "category": category,
        "amount": amount,
        "units": units,
        "ts": time.time(),
        **meta,
    }
    if job_id not in _job_costs:
        _job_costs[job_id] = []
    _job_costs[job_id].append(entry)
    if category == "llm":
        _global_llm += amount
    elif category == "tts":
        _global_tts += amount
    elif category == "video":
        _global_video += amount
    r = _redis()
    if r:
        try:
            key = f"ie:cost:job:{job_id}"
            r.lpush(key, str(amount))
            r.ltrim(key, 0, 999)
            r.expire(key, 86400 * 7)
        except Exception:
            pass


def record_llm_cost(
    job_id: str,
    amount_usd: float,
    input_tokens: int = 0,
    output_tokens: int = 0,
    avatar_id: Optional[str] = None,
) -> None:
    """Record LLM (script generation) cost for a job. Optional avatar_id for per-avatar cost tracking."""
    _record(
        job_id,
        "llm",
        amount_usd,
        units="usd",
        metadata={"input_tokens": input_tokens, "output_tokens": output_tokens},
        avatar_id=avatar_id,
    )


def record_tts_cost(
    job_id: str, amount_usd: float, characters: int = 0, avatar_id: Optional[str] = None
) -> None:
    """Record TTS cost for a job. Optional avatar_id for per-avatar cost tracking."""
    _record(job_id, "tts", amount_usd, units="usd", metadata={"characters": characters}, avatar_id=avatar_id)


def record_video_cost(
    job_id: str, amount_usd: float, duration_sec: float = 0, avatar_id: Optional[str] = None
) -> None:
    """Record video generation cost for a job. Optional avatar_id for per-avatar cost tracking."""
    _record(
        job_id, "video", amount_usd, units="usd", metadata={"duration_sec": duration_sec}, avatar_id=avatar_id
    )


def get_cost_summary() -> Dict[str, Any]:
    """Return global cost summary and per-category totals."""
    return {
        "llm_total_usd": round(_global_llm, 4),
        "tts_total_usd": round(_global_tts, 4),
        "video_total_usd": round(_global_video, 4),
        "total_usd": round(_global_llm + _global_tts + _global_video, 4),
        "jobs_tracked": len(_job_costs),
    }


def get_cost_for_job(job_id: str) -> Dict[str, Any]:
    """Return cost breakdown for one job."""
    entries = _job_costs.get(job_id) or []
    llm = sum(e["amount"] for e in entries if e.get("category") == "llm")
    tts = sum(e["amount"] for e in entries if e.get("category") == "tts")
    video = sum(e["amount"] for e in entries if e.get("category") == "video")
    return {
        "job_id": job_id,
        "llm_usd": round(llm, 4),
        "tts_usd": round(tts, 4),
        "video_usd": round(video, 4),
        "total_usd": round(llm + tts + video, 4),
        "entries": entries,
    }


def get_cost_by_avatar(avatar_id: str) -> Dict[str, Any]:
    """Return cost breakdown for one avatar (sum over all jobs that have this avatar_id in metadata)."""
    llm, tts, video = 0.0, 0.0, 0.0
    jobs_count = 0
    for job_id, entries in _job_costs.items():
        if not any(e.get("avatar_id") == avatar_id for e in entries):
            continue
        jobs_count += 1
        for e in entries:
            if e.get("category") == "llm":
                llm += e.get("amount", 0)
            elif e.get("category") == "tts":
                tts += e.get("amount", 0)
            elif e.get("category") == "video":
                video += e.get("amount", 0)
    return {
        "avatar_id": avatar_id,
        "llm_usd": round(llm, 4),
        "tts_usd": round(tts, 4),
        "video_usd": round(video, 4),
        "total_usd": round(llm + tts + video, 4),
        "jobs_count": jobs_count,
    }


def get_pricing_config() -> Dict[str, Any]:
    """Return unit pricing used for cost calculation (for Cost & Revenue page)."""
    try:
        from .pricing import get_pricing_config as _get
        return _get()
    except Exception:
        return {}


def reset_costs() -> None:
    """Clear in-memory cost data (for tests)."""
    global _global_llm, _global_tts, _global_video
    _job_costs.clear()
    _global_llm = _global_tts = _global_video = 0.0
