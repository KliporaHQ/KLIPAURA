"""
Influencer Engine — Workload balancer.

Distributes jobs across avatars and time to avoid overload.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .content_quota_manager import ContentQuotaManager


class WorkloadBalancer:
    """Balances workload across avatars and respects rate limits."""

    def __init__(self, quota_manager: ContentQuotaManager | None = None):
        self.quota = quota_manager or ContentQuotaManager()

    def should_run_job(
        self,
        avatar_id: str,
        platform: str,
        current_concurrent: int,
        max_concurrent: int,
    ) -> bool:
        """Return True if it's safe to run another job."""
        if current_concurrent >= max_concurrent:
            return False
        return self.quota.can_publish(avatar_id, platform)

    def recommended_slots(
        self,
        avatar_ids: List[str],
        platforms: List[str],
        max_concurrent: int,
    ) -> Dict[str, Any]:
        """Return how many slots to use per avatar/platform to balance."""
        return {
            "avatar_slots": {aid: 1 for aid in avatar_ids},
            "platform_slots": {p: max_concurrent // max(1, len(platforms)) for p in platforms},
            "max_concurrent": max_concurrent,
        }
