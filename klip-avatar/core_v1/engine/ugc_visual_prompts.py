"""
UGC (TikTok/Reels) visual prompts — interaction-first I2V text + scene keys.

Product clips must read as human + product + usage (not motion-only or display-only).
"""

from __future__ import annotations

# Hard override: usage semantics for NO_VISIBLE_PRODUCT_USAGE gate (hands + manipulation).
UGC_I2V_BASE = (
    "close-up of hands actively using the product, clear interaction, "
    "fingers pressing, applying, opening, or demonstrating the product in use, "
    "product centered and dominant in frame, real human usage, natural movement, dynamic hand motion, "
    "camera slightly moving, realistic lighting, no static shot, no slow zoom, "
    "continuous interaction throughout the clip. "
    "the product must be physically manipulated by hands at all times, no idle frames, "
    "no passive display, no product-only shot"
)

# Scene-level verbs: every clip keeps hands + product + interaction (mapped to pipeline scene keys).
SCENE_PROMPTS: dict[str, str] = {
    "hook": (
        "hands opening and picking up the product, unboxing or lifting into frame, "
        "fingers on the product, manipulation visible from the first second"
    ),
    "problem": (
        "hands gripping or touching the product while showing frustration or doubt, "
        "fingers stay on the product, no shot of product alone without hands"
    ),
    "demo": (
        "hands applying the product or using it as intended, clear applying or pressing motion, "
        "usage demonstration not passive display"
    ),
    "benefits": (
        "hands rotating and adjusting the product while showing results, "
        "continuous touching and moving, different angle from demo but still interaction"
    ),
    "cta": (
        "hands holding and presenting the product after use, fingers on product, "
        "interaction through the end of the clip, no product-only hero shot"
    ),
}


def build_ugc_i2v_prompt(scene_key: str) -> str:
    part = SCENE_PROMPTS.get(scene_key, SCENE_PROMPTS["demo"])
    return f"{UGC_I2V_BASE}, {part}"


def default_ugc_scene_types(num_clips: int) -> list[str]:
    """Cycle hook → problem → demo → benefits → cta; always includes at least one demo."""
    cycle = ["hook", "problem", "demo", "benefits", "cta"]
    out = [cycle[i % len(cycle)] for i in range(max(1, num_clips))]
    if "demo" not in out:
        out[-1] = "demo"
    return out


def ensure_demo_scene(scene_types: list[str]) -> None:
    if "demo" not in scene_types:
        raise RuntimeError("NO_DEMO_SCENE")
