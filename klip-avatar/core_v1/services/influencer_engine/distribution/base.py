"""
Influencer Engine — Base distribution connector interface.

publish_video(), fetch_metrics(), delete_post().
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class DistributionConnector(ABC):
    """Interface for platform publishing."""

    @property
    @abstractmethod
    def platform_id(self) -> str:
        pass

    @abstractmethod
    def publish_video(
        self,
        video_url: str,
        title: str,
        description: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Upload/publish video. Returns post_id, url, or error with mock=True if no credentials."""
        pass

    @abstractmethod
    def fetch_metrics(self, post_id: str) -> Dict[str, Any]:
        """Get views, likes, shares, comments, watch_time, etc."""
        pass

    @abstractmethod
    def delete_post(self, post_id: str) -> Dict[str, Any]:
        """Remove published post."""
        pass


def _connector_for_platform(platform: str) -> DistributionConnector:
    platform = (platform or "").lower().replace(" ", "_")
    if "youtube" in platform:
        from .youtube_connector import YouTubeConnector
        return YouTubeConnector()
    if "tiktok" in platform:
        from .tiktok_connector import TikTokConnector
        return TikTokConnector()
    if "instagram" in platform:
        from .instagram_connector import InstagramConnector
        return InstagramConnector()
    if platform in ("x", "twitter"):
        from .x_connector import XConnector
        return XConnector()
    from .youtube_connector import YouTubeConnector
    return YouTubeConnector()


def publish_video(
    platform: str,
    video_url: str,
    title: str,
    description: str = "",
    metadata: Optional[Dict[str, Any]] = None,
    mode: str = "mock",
) -> Dict[str, Any]:
    """
    Publish video to platform.
    mode: "mock" (default, safe) | "real" (use API keys from .env and actually publish).
    """
    meta = dict(metadata or {})
    meta["_distribution_mode"] = mode
    return _connector_for_platform(platform).publish_video(video_url, title, description, meta)


def fetch_metrics(platform: str, post_id: str) -> Dict[str, Any]:
    """Fetch metrics for a post."""
    return _connector_for_platform(platform).fetch_metrics(post_id)


def delete_post(platform: str, post_id: str) -> Dict[str, Any]:
    """Delete post from platform."""
    return _connector_for_platform(platform).delete_post(post_id)
