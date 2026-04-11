"""
Build a single English prompt for WaveSpeed Flux from Avatar Studio fields.
"""

from __future__ import annotations


def build_composite_prompt(
    prompt: str,
    age: str | None = None,
    look: str | None = None,
    outfit: str | None = None,
    personality: str | None = None,
    voice_tone: str | None = None,
) -> str:
    """
    Combine the user's base prompt with optional preset fields into one Flux-friendly portrait prompt.

    Optimized for a clear talking-head reference: front-facing or slight three-quarter, neutral background,
    professional lighting, high detail skin and eyes, square-friendly framing.
    """
    base = (prompt or "").strip()
    parts: list[str] = [
        "Photorealistic professional portrait photograph, head and upper chest visible, "
        "clean neutral or softly blurred background, soft studio key light and subtle rim light, "
        "sharp focus on eyes, natural skin texture, DSLR quality, 85mm lens look, "
        "no text, no watermark, no logos."
    ]
    if base:
        parts.append(base)

    age_s = (age or "").strip()
    look_s = (look or "").strip()
    outfit_s = (outfit or "").strip()
    personality_s = (personality or "").strip()
    tone_s = (voice_tone or "").strip()

    details: list[str] = []
    if age_s:
        details.append(f"Age range: {age_s}.")
    if look_s:
        details.append(f"Appearance: {look_s}.")
    if outfit_s:
        details.append(f"Wardrobe: {outfit_s}.")
    if personality_s:
        details.append(f"On-camera presence: {personality_s}.")
    if tone_s:
        details.append(f"Overall vibe (for character consistency): {tone_s}.")

    if details:
        parts.append(" ".join(details))

    combined = " ".join(parts)
    return combined[:1000]
