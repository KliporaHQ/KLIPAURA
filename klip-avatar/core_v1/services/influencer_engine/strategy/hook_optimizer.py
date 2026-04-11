"""
Influencer Engine — Hook optimizer.

Discovers optimal hooks (openers) from strategy memory.
"""

from __future__ import annotations

from typing import Any, Dict, List

try:
    from ..learning.strategy_memory import get_strategy
except Exception:
    get_strategy = lambda _: {}


class HookOptimizer:
    """Recommends hooks for an avatar/niche."""

    def optimize(
        self,
        avatar_id: str,
        niche: str,
        strategy_memory: Dict[str, Any],
        limit: int = 5,
    ) -> List[str]:
        """Return ordered list of best hooks."""
        best = list((strategy_memory or get_strategy(avatar_id)).get("best_hooks") or [])
        if len(best) >= limit:
            return best[:limit]
        defaults = _default_hooks_for_niche(niche)
        seen = set(best)
        for h in defaults:
            if h not in seen and len(best) < limit:
                best.append(h)
                seen.add(h)
        return best


def _default_hooks_for_niche(niche: str) -> List[str]:
    n = (niche or "").lower()
    if "ai" in n:
        return ["These AI tools will change your life", "5 AI tools nobody told you about"]
    if "crypto" in n:
        return ["This could change how you see crypto", "What most traders still don't know"]
    return ["This one tip changes everything", "What nobody told you about this"]
