"""
Influencer Engine — Asset pipeline helpers.

Register assets from pipeline stages into the asset store and manifest.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .asset_store import save_asset
from .asset_manifest import AssetManifest


def register_assets_from_pipeline(
    context: Dict[str, Any],
    pipeline_source: str = "influencer_engine",
) -> AssetManifest:
    """
    Persist assets from context (script_asset, audio_asset, video_asset, thumbnail_asset)
    into the asset store and return an AssetManifest.
    """
    payload = context.get("payload") or context
    config = payload.get("config") or {}
    avatar_id = config.get("avatar_profile") or ""
    job_id = (payload.get("job_id") or "").strip()

    manifest = AssetManifest(
        job_id=job_id,
        avatar_id=avatar_id,
        pipeline_source=pipeline_source,
    )

    for key, atype in (
        ("script_asset", "script"),
        ("audio_asset", "audio"),
        ("video_asset", "video"),
        ("thumbnail_asset", "thumbnail"),
        ("metadata_asset", "metadata"),
    ):
        raw = payload.get(key) or context.get(key)
        if not raw or not isinstance(raw, dict):
            continue
        url = raw.get("url") or ""
        aid = raw.get("asset_id")
        rec = save_asset(
            asset_type=atype,
            url=url,
            owner_avatar=avatar_id,
            pipeline_source=pipeline_source,
            metadata=raw,
            asset_id=aid,
        )
        setattr(manifest, f"{atype}_id", rec.get("asset_id"))

    return manifest
