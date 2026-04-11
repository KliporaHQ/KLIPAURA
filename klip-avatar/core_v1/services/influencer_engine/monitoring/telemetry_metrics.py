"""
Influencer Engine — Telemetry metrics.

Emit videos_generated_per_hour, success_rate, render_latency, publish_latency,
engagement_rate, revenue_per_video.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

SERVICE_ID = "influencer_engine"
METRICS = [
    "videos_generated_per_hour",
    "success_rate",
    "render_latency",
    "publish_latency",
    "engagement_rate",
    "revenue_per_video",
]


def _emit_event(event_type: str, payload: Dict[str, Any]) -> None:
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


def emit_telemetry(
    metric_name: str,
    value: float | int,
    dimensions: Optional[Dict[str, Any]] = None,
) -> None:
    """Emit one telemetry metric."""
    _emit_event("TELEMETRY_METRIC", {
        "service_id": SERVICE_ID,
        "metric": metric_name,
        "value": value,
        "dimensions": dimensions or {},
    })


class TelemetryMetrics:
    """Tracks and emits pipeline metrics."""

    def __init__(self):
        self._render_start: Optional[float] = None
        self._publish_start: Optional[float] = None

    def start_render(self) -> None:
        self._render_start = time.time()

    def end_render(self) -> None:
        if self._render_start is not None:
            emit_telemetry("render_latency", time.time() - self._render_start)
            self._render_start = None

    def start_publish(self) -> None:
        self._publish_start = time.time()

    def end_publish(self) -> None:
        if self._publish_start is not None:
            emit_telemetry("publish_latency", time.time() - self._publish_start)
            self._publish_start = None

    def record_video_generated(self) -> None:
        emit_telemetry("videos_generated_per_hour", 1, {"unit": "count"})

    def record_success_rate(self, success: bool, total: int = 1) -> None:
        emit_telemetry("success_rate", 1.0 if success else 0.0, {"total": total})

    def record_engagement_rate(self, rate: float) -> None:
        emit_telemetry("engagement_rate", rate)

    def record_revenue_per_video(self, revenue: float) -> None:
        emit_telemetry("revenue_per_video", revenue)
