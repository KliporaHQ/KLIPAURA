"""
Influencer Engine — Production scheduler.

Controls videos_per_day, max_concurrent_jobs; does not overload Worker Runtime.
"""

from __future__ import annotations

import os
from typing import Any, Dict

DEFAULT_VIDEOS_PER_DAY = 10
DEFAULT_MAX_CONCURRENT_JOBS = 3


class ProductionScheduler:
    """Enforces production limits for the content factory."""

    def __init__(
        self,
        videos_per_day: int | None = None,
        max_concurrent_jobs: int | None = None,
    ):
        self.videos_per_day = videos_per_day or int(os.environ.get("IE_VIDEOS_PER_DAY", DEFAULT_VIDEOS_PER_DAY))
        self.max_concurrent_jobs = max_concurrent_jobs or int(
            os.environ.get("IE_MAX_CONCURRENT_JOBS", DEFAULT_MAX_CONCURRENT_JOBS)
        )

    def can_schedule_more(self, scheduled_today: int) -> bool:
        return scheduled_today < self.videos_per_day

    def remaining_capacity_today(self, scheduled_today: int) -> int:
        return max(0, self.videos_per_day - scheduled_today)

    def config(self) -> Dict[str, Any]:
        return {
            "videos_per_day": self.videos_per_day,
            "max_concurrent_jobs": self.max_concurrent_jobs,
        }
