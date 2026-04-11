"""
Influencer Engine — Analytics collector.

Collects views, likes, shares, comments, watch_time, subscriber_growth, engagement_rate.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

METRIC_KEYS = [
    "views",
    "likes",
    "shares",
    "comments",
    "watch_time",
    "subscriber_growth",
    "engagement_rate",
]


class AnalyticsCollector:
    """Collects raw metrics from distribution connectors and events."""

    def __init__(self):
        self._buffer: List[Dict[str, Any]] = []

    def ingest(
        self,
        post_id: str,
        platform: str,
        avatar_id: str = "",
        topic: str = "",
        experiment_id: Optional[str] = None,
        variant: Optional[str] = None,
        metrics: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Ingest one record. metrics can include views, likes, shares, comments, watch_time, etc."""
        try:
            from ..distribution.base import fetch_metrics
            platform_metrics = fetch_metrics(platform, post_id)
        except Exception:
            platform_metrics = {}
        merged = {
            "views": 0,
            "likes": 0,
            "shares": 0,
            "comments": 0,
            "watch_time": 0,
            "subscriber_growth": 0,
            "engagement_rate": 0.0,
        }
        for k in METRIC_KEYS:
            if metrics and k in metrics:
                merged[k] = metrics[k]
            elif platform_metrics:
                v = platform_metrics.get(k) or platform_metrics.get("watch_time_seconds" if k == "watch_time" else k)
                if v is not None:
                    merged[k] = v
        record = {
            "post_id": post_id,
            "platform": platform,
            "avatar_id": avatar_id,
            "topic": topic,
            "experiment_id": experiment_id,
            "variant": variant,
            "metrics": merged,
        }
        self._buffer.append(record)
        return record

    def flush(self) -> List[Dict[str, Any]]:
        out = list(self._buffer)
        self._buffer.clear()
        return out


def collect_metrics(
    post_id: str,
    platform: str,
    avatar_id: str = "",
    topic: str = "",
    metrics: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """One-shot collect and return metrics for a post."""
    c = AnalyticsCollector()
    return c.ingest(post_id, platform, avatar_id, topic, None, None, metrics)
