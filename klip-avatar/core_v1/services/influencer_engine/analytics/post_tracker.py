"""
Influencer Engine — Post-publish tracking.

Tracks post_id, platform, publish_time, metrics_over_time.
update_metrics(post_id) runs periodically (call from cron or feedback loop).
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

REDIS_PREFIX = "ie:post:"
REDIS_METRICS_HISTORY = "ie:post:{}:metrics"  # post_id
REDIS_POST_META = "ie:post:{}:meta"
REDIS_POST_INDEX = "ie:post:index"
MAX_METRICS_SNAPSHOTS = 30


def _redis():
    try:
        from klipaura_core.infrastructure.redis_client import get_redis_client
        return get_redis_client()
    except Exception:
        return None


def register_post(
    post_id: str,
    platform: str,
    publish_time: Optional[float] = None,
    video_id: str = "",
    topic: str = "",
    hook: str = "",
    avatar_id: str = "",
) -> None:
    """Register a published post for tracking."""
    r = _redis()
    ts = publish_time or time.time()
    meta = {
        "post_id": post_id,
        "platform": platform,
        "publish_time": ts,
        "video_id": video_id,
        "topic": topic,
        "hook": hook,
        "avatar_id": avatar_id,
    }
    if r:
        try:
            import json
            key = REDIS_POST_META.format(post_id)
            r.set(key, json.dumps(meta))
            r.expire(key, 86400 * 90)
            r.sadd(REDIS_POST_INDEX, post_id)
        except Exception:
            pass


def update_metrics(
    post_id: str,
    platform: str,
    metrics: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Fetch and store latest metrics for a post (call periodically).
    Uses distribution.fetch_metrics(platform, post_id) when available.
    Returns latest metrics dict (views, likes, comments, watch_time_seconds, engagement_rate).
    """
    try:
        from ..distribution.base import fetch_metrics
    except Exception:
        from services.influencer_engine.distribution.base import fetch_metrics
    latest = fetch_metrics(platform, post_id)
    ts = time.time()
    snapshot = {"timestamp": ts, "metrics": latest}
    r = _redis()
    if r:
        try:
            import json
            key = REDIS_METRICS_HISTORY.format(post_id)
            r.lpush(key, json.dumps(snapshot))
            r.ltrim(key, 0, MAX_METRICS_SNAPSHOTS - 1)
            r.expire(key, 86400 * 90)
        except Exception:
            pass
    return latest


def get_metrics_over_time(post_id: str) -> List[Dict[str, Any]]:
    """Return list of { timestamp, metrics } for post_id."""
    r = _redis()
    out: List[Dict[str, Any]] = []
    if r:
        try:
            import json
            key = REDIS_METRICS_HISTORY.format(post_id)
            raw_list = r.lrange(key, 0, MAX_METRICS_SNAPSHOTS - 1)
            for raw in (raw_list or []):
                try:
                    out.append(json.loads(raw))
                except (json.JSONDecodeError, TypeError):
                    pass
        except Exception:
            pass
    return out


def get_post_meta(post_id: str) -> Optional[Dict[str, Any]]:
    """Return registered meta for post_id."""
    r = _redis()
    if r:
        try:
            import json
            raw = r.get(REDIS_POST_META.format(post_id))
            if raw:
                return json.loads(raw)
        except Exception:
            pass
    return None


def list_tracked_post_ids(limit: int = 100) -> List[str]:
    """Return list of post_ids we have meta for."""
    r = _redis()
    if r:
        try:
            ids = list(r.smembers(REDIS_POST_INDEX) or [])[:limit]
            return [i.decode() if isinstance(i, bytes) else i for i in ids]
        except Exception:
            pass
    return []
