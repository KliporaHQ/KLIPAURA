"""
Resolve WaveSpeed API key the same way Mission Control does (dashboard + workers).

Workers and video_renderer historically read only WAVESPEED_API_KEY; the dashboard also
accepts WAVESPEED_KEY / WAVE_SPEED_API_KEY and reloads core_v1/.env each request.
Use this module anywhere WaveSpeed is called so behavior stays consistent.
"""

from __future__ import annotations

import os
import typing as t

from core.services.env_placeholders import is_configured_secret


def _klipavatar_root() -> str:
    """Core V1 root (directory containing ``engine``, ``services``, ``.env``)."""
    here = os.path.dirname(os.path.abspath(__file__))
    # core_v1/core/services -> core_v1/core -> core_v1
    return os.path.dirname(os.path.dirname(here))


def normalize_wavespeed_api_key_secret(raw: str) -> str:
    """
    Strip accidental Bearer prefix and quotes so Authorization: Bearer <token> matches WaveSpeed.

    Users sometimes paste ``Bearer sk-...`` or quoted values into .env; without this, the header
    becomes ``Bearer Bearer sk-...`` and the API returns HTTP 401 Invalid token.
    """
    s = (raw or "").strip()
    if not s:
        return ""
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        s = s[1:-1].strip()
    while True:
        low = s.lower()
        if low.startswith("bearer "):
            s = s[7:].strip()
            continue
        break
    return s


def reload_klipavatar_dotenv() -> None:
    """Merge Core V1 ``.env`` then optional cwd/.env."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    repo = os.path.join(_klipavatar_root(), ".env")
    if os.path.isfile(repo):
        load_dotenv(repo, verbose=False, override=True)
    cwd_env = os.path.join(os.getcwd(), ".env")
    if os.path.isfile(cwd_env) and os.path.normcase(cwd_env) != os.path.normcase(repo):
        load_dotenv(cwd_env, verbose=False, override=False)


def resolve_wavespeed_api_key() -> t.Tuple[t.Optional[str], str]:
    """
    Return (key_or_none, env_var_name_or_diagnostic).

    Reloads dotenv from repo root first so .env edits match dashboard behavior.
    """
    reload_klipavatar_dotenv()
    for var in ("WAVESPEED_API_KEY", "WAVESPEED_KEY", "WAVE_SPEED_API_KEY"):
        raw = os.environ.get(var)
        if is_configured_secret(raw):
            norm = normalize_wavespeed_api_key_secret(raw or "")
            if is_configured_secret(norm):
                return (norm, var)
    raw = os.environ.get("WAVESPEED_API_KEY")
    if raw is None or not str(raw).strip():
        return (
            None,
            "WAVESPEED_API_KEY is empty — set in core_v1/.env and restart",
        )
    if not is_configured_secret(raw):
        return (
            None,
            "WAVESPEED_API_KEY looks like a placeholder — replace with your real key from wavespeed.ai",
        )
    norm = normalize_wavespeed_api_key_secret(raw or "")
    if is_configured_secret(norm):
        return (norm, "WAVESPEED_API_KEY")
    return (
        None,
        "WAVESPEED_API_KEY is empty after trimming — check for quotes or Bearer prefix in core_v1/.env",
    )
