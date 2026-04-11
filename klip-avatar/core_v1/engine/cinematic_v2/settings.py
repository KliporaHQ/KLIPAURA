"""Feature flags and tunables for Cinematic Engine V2."""

from __future__ import annotations

import os


def is_cinematic_v2_enabled() -> bool:
    """When True, video-render engine may use render_video_v2 (requires script + local voice)."""
    return (os.environ.get("USE_CINEMATIC_V2") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


# Default background music path (optional). Voice remains primary; music is ducked / low gain.
CINEMATIC_BG_MUSIC_PATH = (os.environ.get("CINEMATIC_BG_MUSIC_PATH") or "").strip()

# Crossfade between generated scene clips (seconds)
CINEMATIC_TRANSITION_SEC = float(os.environ.get("CINEMATIC_TRANSITION_SEC") or "0.45")

# Music level relative to full scale (~ -25 dB)
CINEMATIC_MUSIC_LINEAR_GAIN = float(os.environ.get("CINEMATIC_MUSIC_LINEAR_GAIN") or "0.056")

# Stock media (optional) — real clip fetcher uses Pexels first, Pixabay fallback
PEXELS_API_KEY = (os.environ.get("PEXELS_API_KEY") or "").strip()
PIXABAY_API_KEY = (os.environ.get("PIXABAY_API_KEY") or "").strip()

# Optional per-segment BGM (hook / body / CTA). If unset, single CINEMATIC_BG_MUSIC_PATH is used with volume shaping.
CINEMATIC_MUSIC_HOOK_PATH = (os.environ.get("CINEMATIC_MUSIC_HOOK_PATH") or "").strip()
CINEMATIC_MUSIC_BODY_PATH = (os.environ.get("CINEMATIC_MUSIC_BODY_PATH") or "").strip()
CINEMATIC_MUSIC_CTA_PATH = (os.environ.get("CINEMATIC_MUSIC_CTA_PATH") or "").strip()

# Enable fetching stock video/images for scenes (requires at least one API key)
CINEMATIC_FETCH_STOCK = (os.environ.get("CINEMATIC_FETCH_STOCK") or "1").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
