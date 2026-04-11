"""
Influencer Engine — Content quota manager.

Controls avatar_scaling_limit, platform_rate_limit. Prevents overload.
"""

from __future__ import annotations

import os
import time
from collections import defaultdict
from typing import Any, Dict

DEFAULT_AVATAR_SCALING_LIMIT = 5
DEFAULT_PLATFORM_RATE_LIMIT_PER_HOUR = 10

REDIS_PREFIX = "ie:quota:"
KEY_AVATAR_DAY = REDIS_PREFIX + "avatar:{}:{}"
KEY_PLATFORM_HOUR = REDIS_PREFIX + "platform:{}:{}"


def _redis():
    try:
        from klipaura_core.infrastructure.redis_client import get_redis_client
        return get_redis_client()
    except Exception:
        return None


class ContentQuotaManager:
    """Manages per-avatar and per-platform quotas."""

    def __init__(
        self,
        avatar_scaling_limit: int | None = None,
        platform_rate_limit_per_hour: int | None = None,
    ):
        self.avatar_scaling_limit = avatar_scaling_limit or int(
            os.environ.get("IE_AVATAR_SCALING_LIMIT", DEFAULT_AVATAR_SCALING_LIMIT)
        )
        self.platform_rate_limit = platform_rate_limit_per_hour or int(
            os.environ.get("IE_PLATFORM_RATE_LIMIT", DEFAULT_PLATFORM_RATE_LIMIT_PER_HOUR)
        )
        self._local_avatar_day: Dict[str, int] = defaultdict(int)
        self._local_platform_hour: Dict[str, int] = defaultdict(int)

    def can_publish(self, avatar_id: str, platform: str) -> bool:
        """True if within avatar and platform limits."""
        return self.avatar_posts_today(avatar_id) < self.avatar_scaling_limit and \
               self.platform_posts_this_hour(platform) < self.platform_rate_limit

    def avatar_posts_today(self, avatar_id: str) -> int:
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        r = _redis()
        if r:
            try:
                return int(r.get(KEY_AVATAR_DAY.format(avatar_id, today)) or 0)
            except (ValueError, TypeError):
                pass
        return self._local_avatar_day.get(avatar_id, 0)

    def platform_posts_this_hour(self, platform: str) -> int:
        from datetime import datetime, timezone
        hour_key = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H")
        r = _redis()
        if r:
            try:
                return int(r.get(KEY_PLATFORM_HOUR.format(platform, hour_key)) or 0)
            except (ValueError, TypeError):
                pass
        return self._local_platform_hour.get(platform, 0)

    def record_publish(self, avatar_id: str, platform: str) -> None:
        """Record one publish for quota accounting."""
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        hour_key = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H")
        r = _redis()
        if r:
            try:
                r.incr(KEY_AVATAR_DAY.format(avatar_id, today))
                r.expire(KEY_AVATAR_DAY.format(avatar_id, today), 86400 * 2)
                r.incr(KEY_PLATFORM_HOUR.format(platform, hour_key))
                r.expire(KEY_PLATFORM_HOUR.format(platform, hour_key), 3600 * 2)
            except Exception:
                pass
        self._local_avatar_day[avatar_id] = self._local_avatar_day.get(avatar_id, 0) + 1
        self._local_platform_hour[platform] = self._local_platform_hour.get(platform, 0) + 1
