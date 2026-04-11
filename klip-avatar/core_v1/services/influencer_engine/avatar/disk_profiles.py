"""
Load per-avatar identity from ``data/avatars/{avatar_id}/`` (social_config.json, persona.json).

Merged into ``get_avatar_profile`` so Zernio Social Set IDs and handles are available without Redis.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional


def _repo_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(os.path.dirname(here)))


def disk_avatar_dir(avatar_id: str) -> str:
    aid = "".join(c for c in (avatar_id or "").strip() if c.isalnum() or c in ("_", "-"))
    return os.path.join(_repo_root(), "data", "avatars", aid)


def load_disk_avatar_overlay(avatar_id: str) -> Dict[str, Any]:
    """Return fields to merge into avatar profile (empty if no on-disk bundle)."""
    base = disk_avatar_dir(avatar_id)
    if not os.path.isdir(base):
        return {}
    out: Dict[str, Any] = {}
    sc_path = os.path.join(base, "social_config.json")
    if os.path.isfile(sc_path):
        try:
            with open(sc_path, "r", encoding="utf-8") as f:
                sc = json.load(f)
            if isinstance(sc, dict):
                out["social_config"] = sc
                zid = (sc.get("zernio_social_set_id") or sc.get("social_set_id") or "").strip()
                out["zernio_social_set_id"] = zid if zid else "DRAFT"
                h = (sc.get("social_handle") or sc.get("handle") or "").strip()
                if h:
                    out["social_handle"] = h
        except Exception:
            out.setdefault("zernio_social_set_id", "DRAFT")
    else:
        out.setdefault("zernio_social_set_id", "DRAFT")
    persona_path = os.path.join(base, "persona.json")
    if os.path.isfile(persona_path):
        try:
            with open(persona_path, "r", encoding="utf-8") as f:
                persona = json.load(f)
            if isinstance(persona, dict):
                out["disk_persona"] = persona
                if persona.get("description") and not out.get("description"):
                    out["description"] = str(persona.get("description") or "").strip()
                if persona.get("niche") and not out.get("niche"):
                    out["niche"] = str(persona.get("niche") or "").strip()
                if persona.get("handle") and not out.get("social_handle"):
                    out["social_handle"] = str(persona.get("handle") or "").strip()
                if persona.get("avatar_role"):
                    out["avatar_role"] = str(persona.get("avatar_role") or "").strip().lower()
                if persona.get("avatar_usage"):
                    out["avatar_usage"] = str(persona.get("avatar_usage") or "").strip().lower()
                if persona.get("render_layout"):
                    out["render_layout"] = str(persona.get("render_layout") or "").strip().lower()
                if persona.get("name") and not out.get("name"):
                    out["name"] = str(persona.get("name") or "").strip()
        except Exception:
            pass
    return out


def avatar_analytics_dir(avatar_id: str) -> str:
    """Return path to analytics/ folder for this avatar (created if missing)."""
    base = disk_avatar_dir(avatar_id)
    path = os.path.join(base, "analytics")
    os.makedirs(path, exist_ok=True)
    return path


def avatar_videos_dir(avatar_id: str) -> str:
    """Return path to videos/ metadata folder for this avatar (created if missing)."""
    base = disk_avatar_dir(avatar_id)
    path = os.path.join(base, "videos")
    os.makedirs(path, exist_ok=True)
    return path


def append_video_history(avatar_id: str, entry: Dict[str, Any]) -> None:
    """Append a rendered-video entry to the avatar's video_history.json."""
    import json as _json
    analytics = avatar_analytics_dir(avatar_id)
    path = os.path.join(analytics, "video_history.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = _json.load(f)
    except (FileNotFoundError, ValueError):
        data = {"avatar_id": avatar_id, "videos": []}
    if not isinstance(data.get("videos"), list):
        data["videos"] = []
    data["videos"].append(entry)
    from datetime import datetime, timezone
    data["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(path, "w", encoding="utf-8") as f:
        _json.dump(data, f, indent=2)


def list_disk_avatar_ids() -> list[str]:
    root = os.path.join(_repo_root(), "data", "avatars")
    if not os.path.isdir(root):
        return []
    out: list[str] = []
    for name in sorted(os.listdir(root)):
        p = os.path.join(root, name)
        if os.path.isdir(p) and os.path.isfile(os.path.join(p, "social_config.json")):
            out.append(name)
    return out


# Canonical Mission Control identities (bundled catalog + Redis defaults). Not legacy Priya/Arjun.
PRIMARY_SYSTEM_IDENTITIES: tuple[dict[str, str], ...] = (
    {
        "avatar_id": "aria_veda",
        "display_name": "Aria Veda",
        "persona_hint": "aria",
        "avatar_usage": "influencer",
        "social_handle_default": "@AriaVedaAI",
    },
    {
        "avatar_id": "kael_vance",
        "display_name": "Kael Vance",
        "persona_hint": "kael",
        "avatar_usage": "affiliate",
        "social_handle_default": "@VanceTechReview",
    },
)

# Target niches: label + owning persona (Aria = influencer/trends, Kael = affiliate/architect).
TARGET_NICHE_CHOICES: tuple[dict[str, str], ...] = (
    {"id": "fintech_crypto_alpha", "label": "FinTech & Crypto Alpha", "persona": "aria"},
    {"id": "saas_productivity", "label": "SaaS & Productivity", "persona": "kael"},
    {"id": "biohacking_longevity", "label": "BioHacking & Longevity", "persona": "aria"},
    {"id": "smart_home_future", "label": "Smart Home & Future Living", "persona": "kael"},
    {"id": "dubai_luxury_re", "label": "Dubai Luxury Real Estate", "persona": "aria"},
    {"id": "ecommerce_dropshipping", "label": "E-commerce & Dropshipping", "persona": "kael"},
    {"id": "parenting_family_tech", "label": "Parenting & Family Tech", "persona": "kael"},
    {"id": "mental_health_mindfulness", "label": "Mental Health & Mindfulness", "persona": "aria"},
    {"id": "accessible_living_health_tech", "label": "Accessible Living & Health Tech", "persona": "kael"},
    {"id": "remote_work_digital_nomad", "label": "Remote Work & Digital Nomad Lifestyle", "persona": "aria"},
    {"id": "diy_home_improvement_decor", "label": "DIY Home Improvement & Decor", "persona": "kael"},
    {"id": "mens_fashion_grooming", "label": "Men's Fashion & Grooming", "persona": "aria"},
    {
        "id": "fashion_beauty_glam_uae",
        "label": "Fashion & Beauty — UAE glam, skincare & runway",
        "persona": "aria",
    },
    {"id": "pet_care_training", "label": "Pet Care & Training", "persona": "kael"},
    {"id": "vegan_plant_based_nutrition", "label": "Vegan & Plant-Based Nutrition", "persona": "aria"},
    {"id": "language_learning_expat_life", "label": "Language Learning & Expat Life", "persona": "kael"},
    {"id": "ai_content_creator_tools", "label": "AI Content & Creator Tools", "persona": "kael"},
    {"id": "early_childhood_education", "label": "Early Childhood Education", "persona": "kael"},
    {"id": "global_stocks_etfs", "label": "Global Stocks & ETFs", "persona": "aria"},
    {"id": "aviation_aerospace_tech", "label": "Aviation & Aerospace Tech", "persona": "aria"},
    {"id": "luxury_travel_lifestyle", "label": "Luxury Travel & Lifestyle", "persona": "aria"},
    {"id": "sustainable_fashion_beauty", "label": "Sustainable Fashion & Beauty", "persona": "kael"},
    {"id": "fitness_wearable_tech", "label": "Fitness & Wearable Tech", "persona": "kael"},
    {"id": "personal_finance_budgeting", "label": "Personal Finance & Budgeting", "persona": "kael"},
    {"id": "gaming_esports", "label": "Gaming & Esports", "persona": "aria"},
)


def niche_persona(niche_id: str) -> Optional[str]:
    """Return ``aria`` | ``kael`` | None for a niche ``id``."""
    nid = (niche_id or "").strip().lower()
    for row in TARGET_NICHE_CHOICES:
        if row.get("id", "").lower() == nid:
            return str(row.get("persona") or "").lower() or None
    return None


def default_avatar_for_niche(niche_id: str) -> Optional[str]:
    """Pick primary ``avatar_id`` (aria_veda / kael_vance) from niche persona."""
    p = niche_persona(niche_id)
    if p == "aria":
        return "aria_veda"
    if p == "kael":
        return "kael_vance"
    return None


# ── Identity guard ──────────────────────────────────────────────────────────────
_AUTHORIZED_AVATAR_IDS: frozenset[str] = frozenset(
    ident["avatar_id"] for ident in PRIMARY_SYSTEM_IDENTITIES
)

_AUTHORIZED_HANDLES: dict[str, str] = {
    "aria_veda": "@AriaVedaAI",
    "kael_vance": "@VanceTechReview",
}


def is_authorized_avatar_id(avatar_id: str) -> bool:
    """Return True only for the two canonical Venture Lab identities."""
    return (avatar_id or "").strip().lower() in _AUTHORIZED_AVATAR_IDS


def resolve_watermark_handle(avatar_id: str, explicit_handle: str = "") -> str:
    """
    Return the canonical watermark handle for an avatar.

    Priority: explicit handle (if it matches an authorized handle) →
    avatar_id lookup → Aria Veda default.
    """
    aid = (avatar_id or "").strip().lower()
    h = (explicit_handle or "").strip()
    authorized_set = set(_AUTHORIZED_HANDLES.values())
    if h in authorized_set:
        return h
    if aid in _AUTHORIZED_HANDLES:
        return _AUTHORIZED_HANDLES[aid]
    return _AUTHORIZED_HANDLES["aria_veda"]
