"""AvatarLoader — config-driven avatar management.

Replaces the deprecated ``infrastructure.avatar_registry`` module.  All avatar data
is read from ``social_config.json`` files on disk; no avatar IDs, voice IDs, or
affiliate tags are hardcoded anywhere in this file.

Directory resolution order
--------------------------
1. ``AVATARS_DIR`` environment variable (absolute path)
2. ``<repo_root>/klip-avatar/core_v1/data/avatars``   (default)

Registry resolution order
--------------------------
1. ``<repo_root>/data/avatars/registry.json``          (writable registry)
2. Scan ``AVATARS_DIR`` for ``*/social_config.json``   (fallback if registry missing)
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[1]

_VALID_STATUSES = {"active", "paused", "suspended"}


def _default_avatars_dir() -> Path:
    return _REPO_ROOT / "klip-avatar" / "core_v1" / "data" / "avatars"


def _registry_path() -> Path:
    return _REPO_ROOT / "data" / "avatars" / "registry.json"


class AvatarLoader:
    """Load, validate, and query avatar configurations.

    Usage::

        loader = AvatarLoader()
        active = loader.list_active()          # [{"avatar_id": ..., "status": "active", ...}]
        cfg = loader.load("my-avatar")      # full social_config dict
        tag = loader.get_affiliate_tag("my-avatar", "amazon")  # ""
    """

    def __init__(self, avatars_dir: Optional[str] = None) -> None:
        env_dir = (os.getenv("AVATARS_DIR") or "").strip()
        if avatars_dir:
            self._avatars_dir = Path(avatars_dir).resolve()
        elif env_dir:
            self._avatars_dir = Path(env_dir).resolve()
        else:
            self._avatars_dir = _default_avatars_dir()

        self._cache: Dict[str, dict] = {}

    # ── Registry ────────────────────────────────────────────────────────────

    def _load_registry(self) -> List[dict]:
        reg = _registry_path()
        if reg.is_file():
            try:
                data = json.loads(reg.read_text(encoding="utf-8"))
                return data.get("avatars") or []
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("[avatar_loader] registry.json unreadable: %s", exc)
        # Fallback: scan avatars_dir
        return self._scan_avatars_dir()

    def _scan_avatars_dir(self) -> List[dict]:
        entries: List[dict] = []
        if not self._avatars_dir.is_dir():
            return entries
        for child in sorted(self._avatars_dir.iterdir()):
            if not child.is_dir():
                continue
            sc = child / "social_config.json"
            if sc.is_file():
                try:
                    cfg = json.loads(sc.read_text(encoding="utf-8"))
                    entries.append({
                        "avatar_id": cfg.get("avatar_id") or child.name,
                        "status": cfg.get("status", "paused"),
                        "niches": cfg.get("niches", []),
                        "display_name": cfg.get("display_name", child.name),
                    })
                except (OSError, json.JSONDecodeError):
                    pass
        return entries

    # ── Public API ─────────────────────────────────────────────────────────

    def load(self, avatar_id: str) -> dict:
        """Return full ``social_config.json`` dict for *avatar_id*.

        Raises ``KeyError`` if avatar has no social_config.json.
        """
        if avatar_id in self._cache:
            return self._cache[avatar_id]
        sc_path = self._avatars_dir / avatar_id / "social_config.json"
        if not sc_path.is_file():
            raise KeyError(f"No social_config.json for avatar '{avatar_id}' at {sc_path}")
        try:
            cfg = json.loads(sc_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise KeyError(f"social_config.json unreadable for '{avatar_id}': {exc}") from exc
        self._cache[avatar_id] = cfg
        return cfg

    def list_all(self) -> List[dict]:
        """Return registry entries for ALL avatars (any status)."""
        return self._load_registry()

    def list_active(self) -> List[dict]:
        """Return registry entries where ``status == 'active'``."""
        return [e for e in self._load_registry() if e.get("status") == "active"]

    def list_by_niche(self, niche: str) -> List[dict]:
        """Return active avatars whose niches list contains *niche* (case-insensitive)."""
        niche_lc = niche.lower()
        return [
            e for e in self.list_active()
            if any(n.lower() == niche_lc for n in e.get("niches", []))
        ]

    def get_affiliate_tag(self, avatar_id: str, network: str) -> str:
        """Return affiliate tag/ID for *network* from avatar's social_config.

        Returns empty string if not configured.
        """
        try:
            cfg = self.load(avatar_id)
        except KeyError:
            return ""
        affiliate_ids = cfg.get("affiliate_ids") or {}
        return str(affiliate_ids.get(network) or "").strip()

    def get_voice_id(self, avatar_id: str) -> str:
        """Return ElevenLabs voice ID for *avatar_id*. Empty string if not set."""
        try:
            cfg = self.load(avatar_id)
        except KeyError:
            return ""
        return str(cfg.get("elevenlabs_voice_id") or "").strip()

    def get_asset_path(self, avatar_id: str, asset: str) -> str:
        """Return the filesystem path for an asset file (face_image, portrait_image, etc.).

        Returns empty string if not configured or file not found.
        """
        try:
            cfg = self.load(avatar_id)
        except KeyError:
            return ""
        assets = cfg.get("assets") or {}
        filename = str(assets.get(asset) or "").strip()
        if not filename:
            return ""
        full = self._avatars_dir / avatar_id / filename
        return str(full) if full.is_file() else ""

    def pick_default(self) -> Optional[str]:
        """Return avatar_id of the first active avatar, or None."""
        active = self.list_active()
        return active[0]["avatar_id"] if active else None

    def validate_all(self) -> dict:
        """Validate all registry avatars.  Logs warnings.  Returns validation report.

        Returns::

            {
                "valid":   [{"avatar_id": ..., "status": ...}, ...],
                "invalid": [{"avatar_id": ..., "error": ...}, ...],
            }
        """
        valid: List[dict] = []
        invalid: List[dict] = []
        for entry in self._load_registry():
            avatar_id = entry.get("avatar_id", "")
            status = entry.get("status", "unknown")
            sc_path = self._avatars_dir / avatar_id / "social_config.json"
            if not sc_path.is_file():
                # Check for .example only
                ex = self._avatars_dir / avatar_id / "social_config.example.json"
                hint = " (has .example only)" if ex.is_file() else ""
                msg = f"missing social_config.json{hint}"
                logger.warning("[avatar_loader] WARNING: %s %s", avatar_id, msg)
                invalid.append({"avatar_id": avatar_id, "error": msg})
                continue
            try:
                cfg = self.load(avatar_id)
            except KeyError as exc:
                logger.warning("[avatar_loader] WARNING: %s unreadable — %s", avatar_id, exc)
                invalid.append({"avatar_id": avatar_id, "error": str(exc)})
                continue
            # Warn on missing critical fields
            warnings = []
            if not cfg.get("elevenlabs_voice_id"):
                warnings.append("no elevenlabs_voice_id")
            if not cfg.get("affiliate_ids", {}).get("amazon"):
                warnings.append("no affiliate_ids.amazon")
            warn_str = f" [{', '.join(warnings)}]" if warnings else ""
            logger.info("[avatar_loader] valid: %s (%s)%s", avatar_id, status, warn_str)
            valid.append({"avatar_id": avatar_id, "status": status})

        return {"valid": valid, "invalid": invalid}

    def update_status(self, avatar_id: str, new_status: str) -> bool:
        """Update avatar status in registry.json.

        Returns True on success.  The status change persists across restarts.
        """
        if new_status not in _VALID_STATUSES:
            raise ValueError(f"Invalid status '{new_status}'. Must be one of {_VALID_STATUSES}")
        reg = _registry_path()
        if not reg.is_file():
            logger.error("[avatar_loader] registry.json not found at %s", reg)
            return False
        try:
            data = json.loads(reg.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("[avatar_loader] Cannot read registry: %s", exc)
            return False

        avatars = data.get("avatars", [])
        for entry in avatars:
            if entry.get("avatar_id") == avatar_id:
                entry["status"] = new_status
                break
        else:
            logger.error("[avatar_loader] Avatar '%s' not found in registry", avatar_id)
            return False

        from datetime import datetime, timezone
        data["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            reg.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            logger.error("[avatar_loader] Cannot write registry: %s", exc)
            return False

        # Invalidate cache for this avatar so next load() re-reads disk
        self._cache.pop(avatar_id, None)
        return True

    def scan_and_update_registry(self) -> None:
        """Scan avatars_dir and add any new avatars (with social_config.json) to registry.json.

        Existing entries are NOT overwritten.  New entries are added with status=paused.
        """
        reg = _registry_path()
        if reg.is_file():
            try:
                data = json.loads(reg.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {"avatars": []}
        else:
            data = {"avatars": []}

        existing_ids = {e.get("avatar_id") for e in data.get("avatars", [])}
        added = 0
        for entry in self._scan_avatars_dir():
            if entry["avatar_id"] not in existing_ids:
                entry["status"] = "paused"
                data.setdefault("avatars", []).append(entry)
                added += 1
                logger.info("[avatar_loader] Registered new avatar: %s", entry["avatar_id"])

        if added:
            from datetime import datetime, timezone
            data["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            reg.parent.mkdir(parents=True, exist_ok=True)
            reg.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# Module-level convenience instance (lazy — not expensive to construct)
_loader: Optional[AvatarLoader] = None


def get_loader() -> AvatarLoader:
    """Return a module-level singleton AvatarLoader."""
    global _loader
    if _loader is None:
        _loader = AvatarLoader()
    return _loader


__all__ = ["AvatarLoader", "get_loader"]
