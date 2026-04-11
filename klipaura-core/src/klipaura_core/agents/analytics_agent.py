"""Stub for legacy AnalyticsAgent import."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class AnalyticsAgent:
    """No-op stand-in for the old analytics agent."""

    def run(self, *args, **kwargs):
        logger.warning("AnalyticsAgent.run() — stub (klipaura_core.agents not active)")
        return {}

    @staticmethod
    def collect_performance(context: dict = None, **kwargs) -> dict:
        logger.warning("AnalyticsAgent.collect_performance() — stub")
        return {}
