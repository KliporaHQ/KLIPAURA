"""
Influencer Engine — DistributionAgent.

Optimizes platform target for a topic and avatar profile.
"""

from __future__ import annotations

from typing import Any, Dict


class DistributionAgent:
    """Selects optimal platform for content given topic and avatar profile."""

    @staticmethod
    def optimize_platform_target(topic: str, avatar_profile: Dict[str, Any]) -> str:
        """
        Select best platform for this topic and avatar using simple heuristic.

        Heuristic:
            - ai_tools -> youtube_shorts
            - crypto -> x
            - default -> first platform in avatar_profile.platforms or youtube_shorts.

        Returns:
            Platform key (e.g. "youtube_shorts", "x", "tiktok").
        """
        if not avatar_profile:
            return "youtube_shorts"
        niche = (avatar_profile.get("niche") or "").strip().lower()
        platforms = avatar_profile.get("platforms") or []
        if niche == "ai_tools":
            return "youtube_shorts" if "youtube_shorts" in platforms else (platforms[0] if platforms else "youtube_shorts")
        if niche == "crypto":
            return "x" if "x" in platforms else (platforms[0] if platforms else "youtube_shorts")
        return platforms[0] if platforms else "youtube_shorts"


def optimize_platform_target(topic: str, avatar_profile: Dict[str, Any]) -> str:
    """Module-level helper; delegates to DistributionAgent.optimize_platform_target."""
    return DistributionAgent.optimize_platform_target(topic, avatar_profile)
