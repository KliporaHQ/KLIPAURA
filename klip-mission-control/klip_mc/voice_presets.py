"""
Map friendly voice tone labels to ElevenLabs ``voice_id``.

Resolution order:
1. JSON file ``config/voice_tone_presets.json`` (repo root) if present — ``presets`` object label → voice_id.
2. Environment variables ``ELEVENLABS_VOICE_TONE_<KEY>`` where KEY is derived from the label.
3. ``infrastructure.avatar_registry.resolve_elevenlabs_voice_id(avatar_id)`` when ``avatar_id`` is set.
4. Caller uses ``default_env_voice_id()`` as final fallback.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Optional


def _repo_config_path() -> Path:
    here = Path(__file__).resolve().parents[1]
    return (here.parent / "config" / "voice_tone_presets.json").resolve()


def _load_json_presets() -> dict[str, str]:
    path = _repo_config_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    presets = data.get("presets")
    if not isinstance(presets, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in presets.items():
        if isinstance(k, str) and isinstance(v, str) and v.strip():
            out[k.strip().lower()] = v.strip()
    return out


def _label_to_env_suffix(label: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", label.strip().lower()).strip("_")
    return s.upper() or "CUSTOM"


def resolve_voice_id_from_tone(
    voice_tone: str | None,
    *,
    avatar_id: Optional[str] = None,
) -> Optional[str]:
    """
    Return a non-empty ElevenLabs voice id from a preset label, or None to use caller fallback.
    """
    raw = (voice_tone or "").strip()
    if not raw:
        return None

    key = raw.lower()
    file_map = _load_json_presets()
    if key in file_map:
        return file_map[key]

    for fk, vid in file_map.items():
        if fk in key or key in fk:
            return vid

    env_suffix = _label_to_env_suffix(raw)
    ev = (os.getenv(f"ELEVENLABS_VOICE_TONE_{env_suffix}") or "").strip()
    if ev:
        return ev

    fuzzy: list[tuple[str, str]] = [
        ("warm male", "WARM_MALE"),
        ("warm female", "WARM_FEMALE"),
        ("energetic", "ENERGETIC"),
        ("deep confident", "DEEP_CONFIDENT"),
        ("friendly", "FRIENDLY"),
        ("professional", "PROFESSIONAL"),
    ]
    for needle, suffix in fuzzy:
        if needle in key:
            ev2 = (os.getenv(f"ELEVENLABS_VOICE_TONE_{suffix}") or "").strip()
            if ev2:
                return ev2

    if avatar_id:
        try:
            root = Path(__file__).resolve().parents[2]
            if str(root) not in sys.path:
                sys.path.insert(0, str(root))
            from infrastructure.avatar_registry import resolve_elevenlabs_voice_id

            rid = resolve_elevenlabs_voice_id(avatar_id.strip())
            if rid:
                return rid
        except Exception:
            pass

    return None


def default_env_voice_id() -> Optional[str]:
    return (
        (os.getenv("ELEVENLABS_VOICE_ID") or os.getenv("ELEVENLABS_VOICE_ID_ANIKA") or "").strip() or None
    )
