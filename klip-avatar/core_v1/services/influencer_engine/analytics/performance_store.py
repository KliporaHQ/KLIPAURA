"""
Influencer Engine — Performance store (lightweight).

Stores per-video: video_id, topic, hook, platform, metrics, score, timestamp.
Uses Redis when available; fallback in-memory.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

REDIS_PREFIX = "ie:perf:"
REDIS_INDEX = "ie:perf:index"
MAX_LIST = 500


def _redis():
    try:
        from klipaura_core.infrastructure.redis_client import get_redis_client
        return get_redis_client()
    except Exception:
        return None


_memory: List[Dict[str, Any]] = []


def save_performance(
    video_id: str,
    topic: str = "",
    hook: str = "",
    platform: str = "",
    metrics: Optional[Dict[str, Any]] = None,
    score: float = 0.0,
    timestamp: Optional[float] = None,
    avatar_id: str = "",
) -> None:
    """Store one performance record."""
    ts = timestamp or time.time()
    record = {
        "video_id": video_id,
        "topic": topic,
        "hook": hook,
        "platform": platform,
        "metrics": dict(metrics or {}),
        "score": score,
        "timestamp": ts,
        "avatar_id": avatar_id,
    }
    r = _redis()
    if r:
        try:
            key = REDIS_PREFIX + video_id
            r.set(key, json.dumps(record))
            r.expire(key, 86400 * 90)
            r.lpush(REDIS_INDEX, video_id)
            r.ltrim(REDIS_INDEX, 0, MAX_LIST - 1)
        except Exception:
            pass
    _memory.append(record)
    if len(_memory) > MAX_LIST:
        _memory.pop(0)


def get_performance(video_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve one record by video_id."""
    r = _redis()
    if r:
        raw = r.get(REDIS_PREFIX + video_id)
        if raw:
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                pass
    for rec in reversed(_memory):
        if rec.get("video_id") == video_id:
            return rec
    return None


def list_recent(avatar_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """List recent performance records (optionally filter by avatar_id if stored in record)."""
    out: List[Dict[str, Any]] = []
    r = _redis()
    if r:
        try:
            ids = r.lrange(REDIS_INDEX, 0, limit - 1)
            for vid in (ids or []):
                v = vid.decode() if isinstance(vid, bytes) else vid
                rec = get_performance(v)
                if rec:
                    if avatar_id and rec.get("avatar_id") != avatar_id:
                        continue
                    out.append(rec)
        except Exception:
            pass
    if not out:
        for rec in reversed(_memory[-limit:]):
            if avatar_id and rec.get("avatar_id") != avatar_id:
                continue
            out.append(rec)
    return out


def get_recent_metrics(avatar_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Return recent performance records for avatar (for scheduler intelligence)."""
    return list_recent(avatar_id=avatar_id, limit=limit)
