"""Stub for legacy DistributionAgent import."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class DistributionAgent:
    """No-op stand-in for the old CrewAI distribution agent."""

    def run(self, *args, **kwargs):
        logger.warning("DistributionAgent.run() — stub (klipaura_core.agents not active)")
        return {}

    @staticmethod
    def optimize_platform_target(topic: str = "", profile: dict = None, **kwargs) -> dict:
        logger.warning("DistributionAgent.optimize_platform_target() — stub")
        return {}
