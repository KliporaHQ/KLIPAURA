"""
Influencer Engine — TrendAgent.

Discovers trending topics for a niche. Used by the scheduler for trend-driven job generation.
"""

from __future__ import annotations

from typing import Any, Dict, List


class TrendAgent:
    """Discovers and scores trending topics for a given niche."""

    def discover_trends_for_niche(self, niche: str) -> List[Dict[str, Any]]:
        """
        Return multiple candidate topics for the niche, sorted by score (desc).

        Returns:
            List of {"topic": str, "score": float}. Scheduler chooses highest score.
        """
        # Placeholder: in production this would call trend APIs (e.g. Google Trends, Reddit, TikTok).
        # Heuristic fallback by niche.
        fallbacks = {
            "ai_tools": [
                {"topic": "Best AI tools for creators in 2025", "score": 0.82},
                {"topic": "Free AI video editing tools", "score": 0.77},
                {"topic": "AI automation for small business", "score": 0.71},
            ],
            "crypto": [
                {"topic": "Bitcoin ETF impact on price", "score": 0.79},
                {"topic": "Altcoin season indicators", "score": 0.74},
                {"topic": "DeFi yield strategies 2025", "score": 0.68},
            ],
        }
        candidates = fallbacks.get(
            (niche or "").strip().lower(),
            [{"topic": f"Trending in {niche or 'general'}", "score": 0.5}],
        )
        return sorted(candidates, key=lambda x: x.get("score", 0), reverse=True)
