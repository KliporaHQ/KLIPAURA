"""
Influencer Engine — Sponsorship detector.

Detects sponsorship opportunities; emits SPONSORSHIP_OPPORTUNITY.
"""

from __future__ import annotations

from typing import Any, Dict, List

SERVICE_ID = "influencer_engine"

SPONSORSHIP_VIEWS_THRESHOLD = 5000


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


class SponsorshipDetector:
    """Detects when content reaches sponsorship-worthy metrics."""

    def check(
        self,
        avatar_id: str,
        topic: str,
        platform: str,
        views: int,
        engagement_rate: float,
    ) -> Dict[str, Any]:
        """
        If views >= threshold, emit SPONSORSHIP_OPPORTUNITY.
        """
        opportunity = views >= SPONSORSHIP_VIEWS_THRESHOLD
        payload = {
            "service_id": SERVICE_ID,
            "avatar_id": avatar_id,
            "topic": topic,
            "platform": platform,
            "views": views,
            "engagement_rate": engagement_rate,
            "opportunity": opportunity,
        }
        if opportunity:
            _emit("SPONSORSHIP_OPPORTUNITY", payload)
        return payload
