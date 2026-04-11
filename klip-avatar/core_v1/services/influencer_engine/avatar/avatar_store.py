"""
Influencer Engine — Avatar Store.

Persistent store for auto-generated (and optionally suggested) avatars.
Uses Redis when available; fallback in-memory. Manual avatar_profiles.yaml
is not modified; scheduler merges manual + active store avatars.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

REDIS_PREFIX = "ie:avatar:"
REDIS_INDEX = "ie:avatar:index"
REDIS_ACTIVE = "ie:avatar:active"
FILE_STORE_NAME = "avatar_store.json"


def _redis():
    try:
        from klipaura_core.infrastructure.redis_client import get_redis_client
        return get_redis_client()
    except Exception:
        return None


def _file_store_path() -> str:
    base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "..", "config", FILE_STORE_NAME)


_memory: Dict[str, Dict[str, Any]] = {}
_memory_active: set = set()


def _load_file_store() -> Dict[str, Dict[str, Any]]:
    path = _file_store_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("avatars") or data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_file_store(avatars: Dict[str, Dict[str, Any]]) -> None:
    path = _file_store_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"avatars": avatars}, f, indent=2)
            f.flush()
            try:
                os.fsync(f.fileno())
            except (OSError, AttributeError):
                pass
    except Exception as e:
        import sys
        print(f"[avatar_store] _save_file_store failed: {e}", file=sys.stderr)
        raise


def save_avatar(profile: Dict[str, Any]) -> str:
    """
    Save an avatar profile. profile must have avatar_id or one will be generated.
    Returns avatar_id. Avatar is stored as active by default.
    """
    avatar_id = (profile.get("avatar_id") or "").strip() or f"gen_{profile.get('niche', 'general')}"
    profile = dict(profile)
    profile["avatar_id"] = avatar_id
    profile["active"] = profile.get("active", True)

    r = _redis()
    if r:
        try:
            key = REDIS_PREFIX + avatar_id
            r.set(key, json.dumps(profile))
            r.expire(key, 86400 * 365)
            r.sadd(REDIS_ACTIVE, avatar_id)
            r.sadd(REDIS_INDEX, avatar_id)
        except Exception:
            pass
    _memory[avatar_id] = profile
    if profile.get("active", True):
        _memory_active.add(avatar_id)

    # Persist to file for durability when Redis is not used
    file_avatars = _load_file_store()
    file_avatars[avatar_id] = profile
    _save_file_store(file_avatars)
    return avatar_id


def get_avatar(avatar_id: str) -> Optional[Dict[str, Any]]:
    """Return avatar profile by id, or None."""
    r = _redis()
    if r:
        try:
            raw = r.get(REDIS_PREFIX + avatar_id)
            if raw:
                return json.loads(raw)
        except Exception:
            pass
    if avatar_id in _memory:
        return _memory[avatar_id]
    file_avatars = _load_file_store()
    return file_avatars.get(avatar_id)


def list_avatars(active_only: bool = True) -> List[Dict[str, Any]]:
    """List all avatars, optionally only active ones."""
    out: List[Dict[str, Any]] = []
    file_avatars = _load_file_store()
    for aid, profile in file_avatars.items():
        if active_only and not profile.get("active", True):
            continue
        out.append(dict(profile))
    r = _redis()
    if r:
        try:
            ids = list(r.smembers(REDIS_INDEX)) or []
            for raw_id in ids:
                aid = raw_id.decode() if isinstance(raw_id, bytes) else raw_id
                if active_only and not r.sismember(REDIS_ACTIVE, aid):
                    continue
                rec = get_avatar(aid)
                if rec and rec not in out and (not active_only or rec.get("active", True)):
                    out.append(rec)
        except Exception:
            pass
    if not out and _memory:
        for aid, profile in _memory.items():
            if active_only and aid not in _memory_active:
                continue
            out.append(dict(profile))
    return out


def update_avatar(avatar_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Apply updates to an avatar and return the updated profile, or None if not found."""
    current = get_avatar(avatar_id)
    if not current:
        return None
    updated = {**current, **updates}
    updated["avatar_id"] = avatar_id
    save_avatar(updated)
    return get_avatar(avatar_id)


def deactivate_avatar(avatar_id: str) -> bool:
    """Mark avatar as inactive (pause/kill). Returns True if found and updated."""
    current = get_avatar(avatar_id)
    if not current:
        return False
    update_avatar(avatar_id, {"active": False})
    r = _redis()
    if r:
        try:
            r.srem(REDIS_ACTIVE, avatar_id)
        except Exception:
            pass
    _memory_active.discard(avatar_id)
    return True


def delete_avatar(avatar_id: str) -> bool:
    """Permanently remove avatar from store (file, Redis, memory). Returns True if it existed and was removed."""
    aid = (avatar_id or "").strip()
    if not aid:
        return False
    current = get_avatar(aid)
    if not current:
        return False
    _memory.pop(aid, None)
    _memory_active.discard(aid)
    r = _redis()
    if r:
        try:
            r.delete(REDIS_PREFIX + aid)
            r.srem(REDIS_ACTIVE, aid)
            r.srem(REDIS_INDEX, aid)
        except Exception:
            pass
    file_avatars = _load_file_store()
    if aid in file_avatars:
        file_avatars = dict(file_avatars)
        file_avatars.pop(aid, None)
        _save_file_store(file_avatars)
    return True
