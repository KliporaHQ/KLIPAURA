"""
Influencer Engine — Avatar Asset Cache.

Stores and retrieves per-avatar assets for consistency across videos:
- Avatar image (URL or path)
- Voice profile (accent, tone, opening_phrase)
- Style config (brand_style, subtitle_style)

Uses Redis when available; fallback in-memory. Does not modify core/.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

REDIS_PREFIX = "ie:avatar:assets:"
REDIS_VOICE_PREFIX = "ie:avatar_voice:"
FILE_STORE_NAME = "avatar_assets.json"


def _redis():
    try:
        from klipaura_core.infrastructure.redis_client import get_redis_client
        return get_redis_client()
    except Exception:
        return None


def _file_store_path() -> str:
    base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "..", "config", FILE_STORE_NAME)


def _assets_root() -> str:
    """Root for /data/avatar_assets/{avatar_id}/ (face images, variations). Use KLIPAURA_ROOT (or legacy KLIPORA_ROOT) when set."""
    root = (os.environ.get("KLIPAURA_ROOT") or os.environ.get("KLIPORA_ROOT") or "").strip()
    if root and os.path.isdir(root):
        return os.path.join(root, "data", "avatar_assets")
    base = os.path.dirname(os.path.abspath(__file__))
    # avatar/ -> influencer_engine -> services -> repo
    repo = os.path.dirname(os.path.dirname(os.path.dirname(base)))
    return os.path.join(repo, "data", "avatar_assets")


def get_avatar_assets_dir_path(avatar_id: str) -> str:
    """Return path to data/avatar_assets/{avatar_id}/ without creating it."""
    return os.path.join(_assets_root(), avatar_id)


def get_avatar_assets_dir(avatar_id: str) -> str:
    """Return path to data/avatar_assets/{avatar_id}/; creates dir if missing."""
    path = get_avatar_assets_dir_path(avatar_id)
    os.makedirs(path, exist_ok=True)
    return path


_memory: Dict[str, Dict[str, Any]] = {}


def _load_file_store() -> Dict[str, Dict[str, Any]]:
    path = _file_store_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("avatar_assets") or data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_file_store(assets: Dict[str, Dict[str, Any]]) -> None:
    path = _file_store_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"avatar_assets": assets}, f, indent=2)
    except Exception:
        pass


def cache_avatar_assets(
    avatar_id: str,
    *,
    avatar_image_url: Optional[str] = None,
    avatar_image_path: Optional[str] = None,
    voice_profile: Optional[Dict[str, Any]] = None,
    style_config: Optional[Dict[str, Any]] = None,
    sample_video_path: Optional[str] = None,
) -> None:
    """
    Store avatar assets for consistency across videos.
    Merges with existing cache for this avatar_id.
    """
    existing = get_avatar_assets(avatar_id) or {}
    updated = dict(existing)
    if avatar_image_url is not None:
        updated["avatar_image_url"] = avatar_image_url
    if avatar_image_path is not None:
        updated["avatar_image_path"] = avatar_image_path
    if voice_profile is not None:
        updated["voice_profile"] = voice_profile
    if style_config is not None:
        updated["style_config"] = style_config
    if sample_video_path is not None:
        updated["sample_video_path"] = sample_video_path
    updated["avatar_id"] = avatar_id

    r = _redis()
    if r:
        try:
            key = REDIS_PREFIX + avatar_id
            r.set(key, json.dumps(updated))
            r.expire(key, 86400 * 365)
        except Exception:
            pass
    _memory[avatar_id] = updated
    file_assets = _load_file_store()
    file_assets[avatar_id] = updated
    _save_file_store(file_assets)


def get_avatar_assets(avatar_id: str) -> Optional[Dict[str, Any]]:
    """Return cached assets for avatar_id: avatar_image_url, voice_profile, style_config."""
    r = _redis()
    if r:
        try:
            raw = r.get(REDIS_PREFIX + avatar_id)
            if raw:
                return json.loads(raw)
        except Exception:
            pass
    if avatar_id in _memory:
        return dict(_memory[avatar_id])
    file_assets = _load_file_store()
    return dict(file_assets[avatar_id]) if avatar_id in file_assets else None


def remove_avatar_assets(avatar_id: str) -> None:
    """Remove cached assets for avatar_id from file store, memory, and Redis."""
    _memory.pop(avatar_id, None)
    r = _redis()
    if r:
        try:
            r.delete(REDIS_PREFIX + avatar_id)
        except Exception:
            pass
    file_assets = _load_file_store()
    if avatar_id in file_assets:
        file_assets = dict(file_assets)
        file_assets.pop(avatar_id, None)
        _save_file_store(file_assets)


def get_avatar_image_url(avatar_id: str) -> Optional[str]:
    """Convenience: return cached avatar image URL for this avatar."""
    assets = get_avatar_assets(avatar_id)
    return (assets or {}).get("avatar_image_url") if assets else None


def get_voice_profile_cached(avatar_id: str) -> Optional[Dict[str, Any]]:
    """Convenience: return cached voice profile for this avatar."""
    assets = get_avatar_assets(avatar_id)
    return (assets or {}).get("voice_profile") if assets else None


def get_style_config_cached(avatar_id: str) -> Optional[Dict[str, Any]]:
    """Convenience: return cached style config (brand_style) for this avatar."""
    assets = get_avatar_assets(avatar_id)
    return (assets or {}).get("style_config") if assets else None


def generate_avatar_face(profile: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generate avatar face image from profile. Uses visual_profile or visual_identity.
    When profile has a description, the image prompt is built from it for exact match.
    Writes to data/avatar_assets/{avatar_id}/face.png and caches in asset store.
    """
    avatar_id = (profile.get("avatar_id") or profile.get("id") or "").strip()
    if not avatar_id:
        return {"ok": False, "error": "missing avatar_id"}
    visual = profile.get("visual_profile") or profile.get("visual_identity") or {}
    style_id = visual.get("style_consistency_id") or visual.get("consistency_seed") or f"{avatar_id}_seed"
    description = (profile.get("description") or "").strip()
    try:
        from .avatar_visual_generator import generate_avatar_image
    except Exception:
        from services.influencer_engine.avatar.avatar_visual_generator import generate_avatar_image
    out_dir = get_avatar_assets_dir(avatar_id)
    output_path = os.path.join(out_dir, "face.png")
    result = generate_avatar_image(
        visual,
        output_path=output_path,
        style_consistency_id=style_id,
        description=description or None,
    )
    if result.get("path") and os.path.isfile(result["path"]):
        cache_avatar_assets(avatar_id, avatar_image_path=result["path"], style_config=profile.get("brand_style"))
    return {"ok": True, "path": result.get("path"), "url": result.get("url"), "mock": result.get("mock", True)}


def generate_variations(profile: Dict[str, Any], count: int = 2) -> List[Dict[str, Any]]:
    """Generate optional variation images (same profile, different seeds). Not required for MVP."""
    return []


def cache_assets(avatar_id: str) -> None:
    """
    Ensure avatar assets are cached: load from data/avatar_assets/{avatar_id}/ if present,
    merge into Redis/file store.
    """
    out_dir = os.path.join(_assets_root(), avatar_id)
    face_path = os.path.join(out_dir, "face.png")
    if os.path.isfile(face_path):
        existing = get_avatar_assets(avatar_id) or {}
        if not existing.get("avatar_image_path") or not os.path.isfile(existing.get("avatar_image_path") or ""):
            cache_avatar_assets(avatar_id, avatar_image_path=face_path)


def set_avatar_voice(avatar_id: str, voice_config: Dict[str, Any]) -> None:
    """Persist voice mapping for avatar (Redis ie:avatar_voice:{avatar_id}). Reuse across all videos."""
    r = _redis()
    if r:
        try:
            key = REDIS_VOICE_PREFIX + avatar_id
            r.set(key, json.dumps(voice_config))
            r.expire(key, 86400 * 365)
        except Exception:
            pass
    cache_avatar_assets(avatar_id, voice_profile=voice_config)


def get_avatar_voice(avatar_id: str) -> Optional[Dict[str, Any]]:
    """Return persisted voice config for avatar."""
    r = _redis()
    if r:
        try:
            raw = r.get(REDIS_VOICE_PREFIX + avatar_id)
            if raw:
                return json.loads(raw)
        except Exception:
            pass
    return get_voice_profile_cached(avatar_id)
