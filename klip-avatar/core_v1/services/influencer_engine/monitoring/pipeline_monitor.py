"""
Influencer Engine — Pipeline monitor.

Tracks pipeline stage duration and success for observability.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from .telemetry_metrics import emit_telemetry

STAGES = [
    "discover_trend",
    "generate_script",
    "validate_content",
    "generate_avatar",
    "generate_voice",
    "compose_video",
    "publish_content",
    "track_performance",
    "analyze_performance",
]


class PipelineMonitor:
    """Monitors pipeline execution and emits telemetry."""

    def __init__(self):
        self._stage_start: Optional[float] = None
        self._current_stage: Optional[str] = None

    def start_stage(self, stage: str) -> None:
        self._current_stage = stage
        self._stage_start = time.time()

    def end_stage(self, stage: str, success: bool = True) -> None:
        if self._stage_start is not None and self._current_stage == stage:
            duration = time.time() - self._stage_start
            emit_telemetry("pipeline_stage_duration", duration, {"stage": stage, "success": success})
            self._stage_start = None
            self._current_stage = None

    def record_pipeline_complete(self, success: bool, total_duration_seconds: float) -> None:
        emit_telemetry("pipeline_run_duration", total_duration_seconds, {"success": success})
