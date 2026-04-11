"""Load ``config/avatars.json`` — display names and default ElevenLabs voice hints per ``avatar_id``."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]


def avatars_config_path() -> Path:
    override = (os.getenv("AVATARS_CONFIG_PATH") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return _REPO / "config" / "avatars.json"


def load_avatars_config() -> dict[str, Any]:
    path = avatars_config_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def default_avatar_id() -> str:
    data = load_avatars_config()
    d = str(data.get("default_avatar_id") or "").strip()
    return d or "theanikaglow"


def get_registry_entry(avatar_id: str) -> dict[str, Any]:
    data = load_avatars_config()
    av = data.get("avatars")
    if not isinstance(av, dict):
        return {}
    entry = av.get(avatar_id)
    return dict(entry) if isinstance(entry, dict) else {}


def resolve_elevenlabs_voice_id(avatar_id: str) -> str | None:
    """Non-empty ``elevenlabs_voice_id`` from registry only (disk persona handled by worker defaults)."""
    entry = get_registry_entry(avatar_id)
    v = str(entry.get("elevenlabs_voice_id") or "").strip()
    return v or None


def display_name_for_avatar(avatar_id: str) -> str:
    entry = get_registry_entry(avatar_id)
    dn = str(entry.get("display_name") or "").strip()
    return dn or avatar_id
