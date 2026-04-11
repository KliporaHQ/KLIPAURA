"""
Influencer Engine — Avatar Generator.

Generates avatar profiles from opportunity data (niche, trends, audience).
Used when high-opportunity is detected and auto_create_avatars or suggestion is enabled.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Dict, List

# Persona templates by niche (deterministic fallback when no LLM)
PERSONA_BY_NICHE = {
    "ai_tools": {
        "tone": "futuristic educator",
        "style": "fast-paced, high-energy",
        "hook_style": "curiosity-driven",
    },
    "crypto": {
        "tone": "analytical trader",
        "style": "data-driven, confident",
        "hook_style": "contrarian take",
    },
    "general": {
        "tone": "engaging creator",
        "style": "conversational, punchy",
        "hook_style": "curiosity-driven",
    },
}

PLATFORMS_BY_NICHE = {
    "ai_tools": ["youtube_shorts", "tiktok", "instagram_reels"],
    "crypto": ["youtube_shorts", "x"],
    "general": ["youtube_shorts", "tiktok", "instagram_reels"],
}

DEFAULT_POSTING_FREQUENCY = 3

# Visual profile defaults by niche (for consistent AI influencer identity)
VISUAL_PROFILE_BY_NICHE = {
    "ai_tools": {
        "gender": "female",
        "age_range": "22-25",
        "ethnicity": "south_indian",
        "skin_tone": "fair",
        "face_features": "sharp, pleasing, homely",
        "attire": "traditional south indian half saree",
        "style_consistency_id": None,  # set to avatar_seed_id when generated
    },
    "crypto": {
        "gender": "neutral",
        "age_range": "25-35",
        "ethnicity": "diverse",
        "skin_tone": "medium",
        "face_features": "confident, professional",
        "attire": "smart casual",
        "style_consistency_id": None,
    },
    "general": {
        "gender": "neutral",
        "age_range": "22-30",
        "ethnicity": "diverse",
        "skin_tone": "medium",
        "face_features": "friendly, approachable",
        "attire": "casual",
        "style_consistency_id": None,
    },
}

VOICE_PROFILE_BY_NICHE = {
    "ai_tools": {
        "accent": "south_indian",
        "tone": "warm, friendly",
        "energy": "medium",
        "opening_phrase": "Vanakkam Makkalae",
    },
    "crypto": {
        "accent": "neutral",
        "tone": "analytical, confident",
        "energy": "medium",
        "opening_phrase": "Hey everyone",
    },
    "general": {
        "accent": "neutral",
        "tone": "engaging, friendly",
        "energy": "medium",
        "opening_phrase": "Hey there",
    },
}

BRAND_STYLE_DEFAULTS = {
    "color_palette": ["#FFB6C1", "#8B4513"],
    "subtitle_style": "bold centered",
    "background_style": "minimal aesthetic",
}


def _slug(s: str) -> str:
    """Safe ID segment from string."""
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_") or "gen"


def generate_avatar_profile(opportunity: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generate an avatar profile from an opportunity (niche, trend_topics, audience).

    Input (opportunity) may contain:
        - niche: str
        - trend_topics: List[str] (optional)
        - audience: str (optional)
        - trend_score: float (optional)

    Output matches the shape expected by scheduler and pipeline (compatible with
    avatar_profiles.yaml entries plus persona and metadata).
    """
    niche = (opportunity.get("niche") or "").strip() or "general"
    trend_topics = opportunity.get("trend_topics") or []
    audience = (opportunity.get("audience") or "").strip() or "general audience"
    trend_score = float(opportunity.get("trend_score") or 0.5)

    # Persona from niche template; can be extended later with LLM
    persona_map = PERSONA_BY_NICHE.get(niche, PERSONA_BY_NICHE["general"])
    platforms = PLATFORMS_BY_NICHE.get(niche, PLATFORMS_BY_NICHE["general"])
    posting = min(5, max(1, int(trend_score * 4) or DEFAULT_POSTING_FREQUENCY))

    # Unique avatar_id: niche slug + short uuid
    base = _slug(niche)
    short_uuid = uuid.uuid4().hex[:6]
    avatar_id = f"{base}_{short_uuid}"

    visual_defaults = VISUAL_PROFILE_BY_NICHE.get(niche, VISUAL_PROFILE_BY_NICHE["general"])
    voice_defaults = VOICE_PROFILE_BY_NICHE.get(niche, VOICE_PROFILE_BY_NICHE["general"])
    signature_phrase = voice_defaults.get("opening_phrase", "Hey there")

    visual_profile = {
        **dict(visual_defaults),
        "style_consistency_id": f"{avatar_id}_seed",
    }

    profile = {
        "avatar_id": avatar_id,
        "niche": niche,
        "tone": persona_map.get("tone", "engaging creator"),
        "persona": {
            "tone": persona_map.get("tone", "engaging creator"),
            "style": persona_map.get("style", "conversational, punchy"),
            "hook_style": persona_map.get("hook_style", "curiosity-driven"),
        },
        "visual_profile": visual_profile,
        "voice_profile": dict(voice_defaults),
        "signature_phrase": signature_phrase,
        "brand_style": dict(BRAND_STYLE_DEFAULTS),
        "platforms": platforms,
        "posting_frequency_per_day": posting,
        "source": "avatar_generator",
        "trend_topics": trend_topics[:5] if isinstance(trend_topics, list) else [],
        "audience": audience,
    }
    return profile
