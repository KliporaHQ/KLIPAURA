"""
GetLate (Zernio) publishing bridge for influencer distribution connectors.

When GETLATE_API_KEY is set (or Redis GetLate config is enabled), real mode
publishes through GetLate instead of legacy per-platform OAuth mocks.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional


def getlate_configured() -> bool:
    if (os.environ.get("GETLATE_API_KEY") or "").strip():
        return True
    try:
        from core.services.getlate_config import get_getlate_config

        cfg = get_getlate_config()
        return bool(cfg.get("getlate_enabled") and cfg.get("getlate_api_key"))
    except Exception:
        return False


def connector_platform_to_getlate_targets(platform_id: str) -> List[str]:
    """Single-platform list for multichannel API."""
    pid = (platform_id or "").lower()
    if "youtube" in pid:
        return ["youtube"]
    if "tiktok" in pid:
        return ["tiktok"]
    if "instagram" in pid:
        return ["instagram"]
    if pid in ("x", "twitter"):
        return ["twitter"]
    return ["youtube"]


def publish_through_getlate(
    platform_id: str,
    video_url: str,
    title: str,
    description: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Publish via GetLate. Returns distribution result dict or None if GetLate unavailable / failed.
    """
    if not getlate_configured():
        return None
    try:
        from services.integrations.getlate_client import publish_multichannel
    except Exception:
        return None

    meta = dict(metadata or {})
    hashtags = str(meta.get("hashtags") or "").strip()
    targets = connector_platform_to_getlate_targets(platform_id)
    res = publish_multichannel(video_url, title, description, hashtags, platforms=targets)
    if not res.get("ok"):
        return None

    post_id = None
    url = None
    if res.get("mode") == "v1_posts":
        body = res.get("response") or {}
        data = body.get("data") if isinstance(body.get("data"), dict) else body
        post_id = str(
            (data or {}).get("id")
            or (data or {}).get("postId")
            or (data or {}).get("post_id")
            or "getlate_v1"
        )
        url = (data or {}).get("url") or (data or {}).get("permalink")
    else:
        plat = res.get("platforms") or {}
        key = targets[0] if targets else "youtube"
        raw = plat.get(key)
        if raw:
            post_id = str(raw)
            url = raw if isinstance(raw, str) and raw.startswith("http") else None

    return {
        "post_id": post_id or f"getlate_{hash(video_url) % 10**10}",
        "url": url or "https://getlate.dev/dashboard",
        "mock": False,
        "platform": platform_id,
        "getlate": True,
        "detail": res,
    }
