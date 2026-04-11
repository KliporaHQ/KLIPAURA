"""
Influencer Engine — Revenue optimizer.

Detects high-performing topics, ad revenue potential; emits REVENUE_OPTIMIZATION_EVENT.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

SERVICE_ID = "influencer_engine"


def _emit(event_type: str, payload: Dict[str, Any]) -> None:
    try:
        from core.service_manager.utils.service_utils import event_publish
        event_publish(event_type, payload)
    except Exception:
        pass
    try:
        from core.service_manager.utils.event_bus_publisher import get_publisher
        pub = get_publisher()
        if pub is not None:
            pub.publish(event_type, payload, source=SERVICE_ID)
    except Exception:
        pass


class RevenueOptimizer:
    """Identifies high-performing topics and revenue opportunities."""

    def analyze(
        self,
        metrics_by_topic: Dict[str, Any],
        metrics_by_avatar: Dict[str, Any],
        threshold_views: int = 1000,
    ) -> Dict[str, Any]:
        """
        Find high-performing topics and emit REVENUE_OPTIMIZATION_EVENT.
        """
        high_topics = [
            t for t, m in metrics_by_topic.items()
            if (m.get("views") or 0) >= threshold_views
        ]
        high_avatars = [
            a for a, m in metrics_by_avatar.items()
            if (m.get("views") or 0) >= threshold_views
        ]
        payload = {
            "service_id": SERVICE_ID,
            "high_performing_topics": high_topics,
            "high_performing_avatars": high_avatars,
            "threshold_views": threshold_views,
        }
        _emit("REVENUE_OPTIMIZATION_EVENT", payload)
        return payload

    def update_revenue_strategy(
        self,
        metrics: Dict[str, Any],
        revenue: float,
    ) -> Dict[str, Any]:
        """
        Update strategy from real metrics and revenue: topics that generate revenue,
        platforms with higher ROI, content length vs earnings.
        Emits REVENUE_OPTIMIZATION_EVENT with strategy hints.
        """
        views = int(metrics.get("views") or 0)
        platform = str(metrics.get("platform") or "")
        topic = str(metrics.get("topic") or "")
        payload = {
            "service_id": SERVICE_ID,
            "revenue": revenue,
            "views": views,
            "platform": platform,
            "topic": topic,
            "revenue_per_1k_views": round(revenue / (views / 1000), 4) if views else 0,
        }
        _emit("REVENUE_OPTIMIZATION_EVENT", payload)
        return payload
