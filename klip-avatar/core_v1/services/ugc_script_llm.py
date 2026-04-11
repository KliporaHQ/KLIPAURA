"""
UGC review-style script: Groq generation + hard validation (fail fast).
"""

from __future__ import annotations

import json
import os
import re
import typing as t

_DEFAULT_CTA_REQUIRED = "Get yours - Link in bio | anikaglow-20"


def _cta_required() -> str:
    raw = (
        (os.environ.get("UGC_CTA_LINE") or "").strip()
        or (os.environ.get("AFFILIATE_CTA_OVERLAY") or "").strip()
        or _DEFAULT_CTA_REQUIRED
    )
    return raw.strip() or _DEFAULT_CTA_REQUIRED


UGC_SCRIPT_SYSTEM_TEMPLATE = """Write a natural, conversational script like a real person sharing experience.

Rules:
- 120–150 words ONLY (required for ~45s+ voiceover)
- Casual tone (I've, it's, don't)
- Sounds like personal discovery
- NOT like an ad
- NOT formal

Structure:
1. Hook (curiosity, no product name) — the first sentence (before . ! or ?) must be 12 words or fewer
2. Problem (relatable)
3. Discovery (found product)
4. Demo (how it works)
5. Benefits (real-life impact)
6. CTA (link in bio)

Use emotional tone and realism.

You MUST include this exact spoken CTA line verbatim (punctuation as shown):
{cta_line}

Output ONLY the script text. No title, no bullets, no stage directions."""

BANNED = ("temu", "aliexpress", "shein", "wish", "alibaba", "dhgate")

def _system_prompt() -> str:
    override = (os.environ.get("UGC_SCRIPT_SYSTEM_OVERRIDE") or "").strip()
    if override:
        return override
    return UGC_SCRIPT_SYSTEM_TEMPLATE.format(cta_line=_cta_required())


def validate_ugc_script(script: str) -> None:
    script = (script or "").strip()
    script_words = script.split()
    if len(script_words) < 120:
        raise RuntimeError("SCRIPT_TOO_SHORT_FOR_DURATION")
    if len(script_words) > 150:
        raise RuntimeError("INVALID_SCRIPT_LENGTH")
    first_sentence = re.split(r"[.!?]", script, maxsplit=1)[0]
    hook = first_sentence.strip()
    if len(hook.split()) > 12:
        raise RuntimeError("WEAK_HOOK")
    low = script.lower()
    if any(m in low for m in BANNED):
        raise RuntimeError("BANNED_MERCHANT")
    cta = _cta_required()
    if cta.lower() not in low:
        raise RuntimeError("MISSING_CTA_LINE")


def _affiliate_link_for_prompt() -> str:
    raw = (os.environ.get("KLIP_AFFILIATE_LINK") or "").strip()
    if raw:
        return raw
    j = (os.environ.get("KLIP_AFFILIATE_DATA") or "").strip()
    if not j:
        return ""
    try:
        d = json.loads(j)
        if isinstance(d, dict):
            return str(d.get("affiliate_link") or "").strip()
    except json.JSONDecodeError:
        pass
    return ""


def generate_ugc_product_script(
    title: str,
    bullets: list[str],
    product_url: str,
) -> str:
    from services.ai.groq_client import _chat

    cta = _cta_required()
    ctx = f"Product page title: {title}\nURL: {product_url}\nBullets:\n" + "\n".join(f"- {b}" for b in bullets[:8])
    aff = _affiliate_link_for_prompt()
    if aff:
        ctx += (
            "\n\nAffiliate tracking: include a strong closing call-to-action that references this exact link "
            f"(read naturally; the verbatim required CTA line below already contains the destination):\n{aff}\n"
        )
    messages: list[dict[str, str]] = [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": ctx},
    ]
    _retryable = frozenset(
        {"SCRIPT_TOO_SHORT_FOR_DURATION", "WEAK_HOOK", "INVALID_SCRIPT_LENGTH", "MISSING_CTA_LINE"}
    )
    for attempt in range(3):
        raw = _chat(messages)
        if not raw:
            raise RuntimeError("UGC_SCRIPT_LLM_FAILED")
        text = re.sub(r"^\s*[\[\(].*?[\]\)]\s*", "", raw, flags=re.DOTALL).strip()
        text = text.strip().strip('"').strip("'")
        try:
            validate_ugc_script(text)
            return text
        except RuntimeError as e:
            code = str(e)
            if code not in _retryable or attempt == 2:
                raise
            messages.append({"role": "assistant", "content": text})
            if code == "SCRIPT_TOO_SHORT_FOR_DURATION":
                fix = "Your reply was too short. Output ONLY the script again with 120–150 words (count them). Same structure and CTA line."
            elif code == "WEAK_HOOK":
                fix = (
                    "The first sentence (hook) must be 12 words or fewer before the first . ! or ? "
                    "Keep total script 120–150 words and the exact CTA line."
                )
            elif code == "INVALID_SCRIPT_LENGTH":
                fix = "Script must be 120–150 words only. Rewrite to fit; keep the exact CTA line."
            else:
                fix = f"Fix validation: {code}. Include verbatim: {cta}"
            messages.append({"role": "user", "content": fix})
