"""
Influencer Engine — Topic optimizer.

Discovers optimal topics from strategy memory and trend feedback.
"""

from __future__ import annotations

from typing import Any, Dict, List

try:
    from ..learning.strategy_memory import get_strategy
except Exception:
    get_strategy = lambda _: {}


class TopicOptimizer:
    """Recommends topics for an avatar/niche."""

    def optimize(
        self,
        avatar_id: str,
        niche: str,
        strategy_memory: Dict[str, Any],
        limit: int = 10,
    ) -> List[str]:
        """Return ordered list of best topics (from strategy best_topics, then fallback)."""
        best = list((strategy_memory or get_strategy(avatar_id)).get("best_topics") or [])
        if len(best) >= limit:
            return best[:limit]
        # Fallback: add niche-based defaults
        defaults = _default_topics_for_niche(niche)
        seen = set(best)
        for t in defaults:
            if t not in seen and len(best) < limit:
                best.append(t)
                seen.add(t)
        return best


def _default_topics_for_niche(niche: str) -> List[str]:
    n = (niche or "").lower()
    if "ai" in n or "tools" in n:
        return ["AI productivity", "AI tools 2024", "ChatGPT tips", "Automation"]
    if "crypto" in n:
        return ["Crypto news", "DeFi", "Bitcoin", "Altcoins"]
    return ["Trending tips", "How-to", "Life hacks"]
