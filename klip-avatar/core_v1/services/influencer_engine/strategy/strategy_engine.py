"""
Influencer Engine — Strategy engine.

Discovers optimal topics, hooks, platforms, posting time, video length.
Output: content_strategy. Scheduler loads strategy before generating jobs.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .topic_optimizer import TopicOptimizer
from .hook_optimizer import HookOptimizer
from .platform_optimizer import PlatformOptimizer
from .posting_time_optimizer import PostingTimeOptimizer


class StrategyEngine:
    """Builds content_strategy from strategy memory and analytics."""

    def __init__(self):
        self._topic_opt = TopicOptimizer()
        self._hook_opt = HookOptimizer()
        self._platform_opt = PlatformOptimizer()
        self._posting_opt = PostingTimeOptimizer()

    def get_content_strategy(
        self,
        avatar_id: str,
        niche: str = "",
        limit_topics: int = 10,
        limit_hooks: int = 5,
    ) -> Dict[str, Any]:
        """
        Return content_strategy: best topics, hooks, platform, posting_time, video_length.
        """
        try:
            from ..learning.strategy_memory import get_strategy
            strategy_memory = get_strategy(avatar_id)
        except Exception:
            strategy_memory = {}

        topics = self._topic_opt.optimize(avatar_id, niche, strategy_memory, limit=limit_topics)
        hooks = self._hook_opt.optimize(avatar_id, niche, strategy_memory, limit=limit_hooks)
        platform = self._platform_opt.optimize(avatar_id, strategy_memory)
        posting_time = self._posting_opt.optimize(avatar_id, strategy_memory)
        video_length = self._posting_opt.recommended_video_length(avatar_id, strategy_memory)

        return {
            "avatar_id": avatar_id,
            "niche": niche or strategy_memory.get("niche", ""),
            "topics": topics,
            "hooks": hooks,
            "platform": platform,
            "posting_time": posting_time,
            "video_length_seconds": video_length,
            "content_strategy": True,
        }


def get_content_strategy(
    avatar_id: str,
    niche: str = "",
    limit_topics: int = 10,
    limit_hooks: int = 5,
) -> Dict[str, Any]:
    """Module-level helper. Scheduler should load this before generating jobs."""
    return StrategyEngine().get_content_strategy(avatar_id, niche, limit_topics, limit_hooks)
