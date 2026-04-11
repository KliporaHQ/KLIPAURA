"""Influencer Engine — metrics."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict

SERVICE_ID = "influencer_engine"
REDIS_PREFIX = "ie:"
KEY_AVATAR_DAY = REDIS_PREFIX + "avatar:{}:{}"
KEY_VIDEOS_TODAY = REDIS_PREFIX + "metrics:videos_today"
KEY_REVENUE = REDIS_PREFIX + "metrics:total_revenue"
KEY_ENGAGEMENT_SUM = REDIS_PREFIX + "metrics:engagement_sum"
KEY_ENGAGEMENT_COUNT = REDIS_PREFIX + "metrics:engagement_count"
KEY_PROCESSING_RATE = REDIS_PREFIX + "metrics:processing_rate"
KEY_WORKER_UTILIZATION = REDIS_PREFIX + "metrics:worker_utilization"
KEY_E2E_LATENCY_AVG = REDIS_PREFIX + "metrics:e2e_latency_avg"


def _redis():
    try:
        from klipaura_core.infrastructure.redis_client import get_redis_client
        return get_redis_client()
    except Exception:
        return None


def collect_metrics(data: dict) -> dict:
    return {"videos": data.get("videos", 0), "ok": True}


def get_service_metrics() -> Dict[str, Any]:
    """
    Mission Control metrics: active_avatars, videos_generated_today,
    avg_engagement_rate, total_revenue, system_health.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    r = _redis()

    # Active avatars: from config
    try:
        from .scheduler.influencer_scheduler import _load_avatar_profiles
        profiles = _load_avatar_profiles()
        active_avatars = len(profiles.get("avatars") or {})
    except Exception:
        active_avatars = 0

    videos_generated_today = 0
    total_revenue = 0.0
    engagement_sum = 0.0
    engagement_count = 0
    if r:
        try:
            videos_generated_today = int(r.get(KEY_VIDEOS_TODAY) or 0)
            total_revenue = float(r.get(KEY_REVENUE) or 0)
            engagement_sum = float(r.get(KEY_ENGAGEMENT_SUM) or 0)
            engagement_count = int(r.get(KEY_ENGAGEMENT_COUNT) or 0)
        except (ValueError, TypeError):
            pass

    avg_engagement_rate = (engagement_sum / engagement_count) if engagement_count else 0.0

    try:
        from .monitoring.health_checks import health_check
        system_health = health_check().get("status", "unknown")
    except Exception:
        system_health = "unknown"

    queue_depth = 0
    try:
        from klipaura_core.infrastructure.queue.queue_factory import get_queue
        q = get_queue("job")
        queue_depth = getattr(q, "get_queue_depth", lambda: getattr(q, "size", lambda: 0)())()
    except Exception:
        pass

    processing_rate = 0.0
    worker_utilization = 0.0
    end_to_end_latency = None
    if r:
        try:
            processing_rate = float(r.get(KEY_PROCESSING_RATE) or 0)
            worker_utilization = float(r.get(KEY_WORKER_UTILIZATION) or 0)
            raw_lat = r.get(KEY_E2E_LATENCY_AVG)
            end_to_end_latency = round(float(raw_lat), 3) if raw_lat is not None else None
        except (ValueError, TypeError):
            pass

    return {
        "service_id": SERVICE_ID,
        "active_avatars": active_avatars,
        "videos_generated_today": videos_generated_today,
        "avg_engagement_rate": round(avg_engagement_rate, 4),
        "total_revenue": round(total_revenue, 2),
        "system_health": system_health,
        "queue_depth": queue_depth,
        "processing_rate": round(processing_rate, 2),
        "worker_utilization": round(worker_utilization, 4),
        "end_to_end_latency": end_to_end_latency,
    }
