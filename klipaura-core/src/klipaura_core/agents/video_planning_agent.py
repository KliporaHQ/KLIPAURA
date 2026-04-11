"""Stub for legacy video_planning_agent import."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def plan_video(*args, **kwargs) -> dict:
    logger.warning("plan_video() — stub (klipaura_core.agents not active)")
    return {}
