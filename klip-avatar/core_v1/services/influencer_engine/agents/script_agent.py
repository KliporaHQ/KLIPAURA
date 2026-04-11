"""
Influencer Engine — Script agent.

Generates short-form video scripts via LLM (GROQ/OpenAI from .env).
Cognitive hardening: avatar identity is injected into system prompt so the LLM
cannot hallucinate persona. Structured JSON output requested with fallback to text parsing.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Any, Dict, Optional

_AVATAR_PROFILES_YAML_SNAPSHOT: str = ""

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# Strict schema keys for script output (pipeline expects these)
SCRIPT_JSON_KEYS = ("hook", "main_content", "cta", "hashtags")

SCRIPT_PROMPT_TEMPLATE = """Create a short-form video script (30 seconds) for:

Topic: {topic}
Persona: {persona}
Hook: {hook}
{signature_instruction}

Format your response exactly as:
HOOK:
(first 3 seconds - one punchy line)

MAIN:
(2-3 short paragraphs, engaging and concise)

CTA:
(one clear call to action)

HASHTAGS:
(comma-separated hashtags, no # in the response)

Reply with valid JSON only:
{{"hook": "...", "main_content": "...", "cta": "...", "hashtags": "...", "compliance_pass": true or false, "compliance_reason": "brief legal note"}}
"""


def _avatar_profiles_yaml_snapshot() -> str:
    """Full catalog from disk — hardcoded reference for anti-hallucination (truncated if huge)."""
    global _AVATAR_PROFILES_YAML_SNAPSHOT
    if _AVATAR_PROFILES_YAML_SNAPSHOT:
        return _AVATAR_PROFILES_YAML_SNAPSHOT
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        cfg = os.path.join(here, "..", "config", "avatar_profiles.yaml")
        cfg = os.path.normpath(cfg)
        if os.path.isfile(cfg):
            with open(cfg, "r", encoding="utf-8") as f:
                _AVATAR_PROFILES_YAML_SNAPSHOT = f.read()
    except Exception:
        _AVATAR_PROFILES_YAML_SNAPSHOT = ""
    return _AVATAR_PROFILES_YAML_SNAPSHOT


def _build_avatar_identity_system_prompt(avatar_profile: Optional[Dict[str, Any]]) -> str:
    """
    Hardcode exact avatar identity from avatar_profiles.yaml into the system prompt
    so the agent has zero room to hallucinate the avatar's identity.
    """
    try:
        from services.ai.groq_client import GROQ_LEGAL_TEAM_SYSTEM

        legal = GROQ_LEGAL_TEAM_SYSTEM
    except Exception:
        legal = ""
    base = (
        (legal + "\n\n") if legal else ""
    ) + (
        "You write short, punchy scripts for 30-second vertical videos. "
        "Reply only with the requested format (HOOK, MAIN, CTA, HASHTAGS) or valid JSON. "
        "If a specific opening phrase is requested, the narrated script MUST start with it verbatim."
    )
    if not avatar_profile or not isinstance(avatar_profile, dict):
        return base
    parts = [base, "\n\n--- AVATAR IDENTITY (do not invent or change) ---"]
    name = avatar_profile.get("name") or avatar_profile.get("avatar_id") or ""
    if name:
        parts.append(f"Name: {name}")
    niche = avatar_profile.get("niche") or ""
    if niche:
        parts.append(f"Niche: {niche}")
    tone = avatar_profile.get("tone") or ""
    if tone:
        parts.append(f"Tone: {tone}")
    desc = (avatar_profile.get("description") or "").strip()
    if desc:
        parts.append(f"Description: {desc[:500]}")
    sig = (avatar_profile.get("signature_phrase") or "").strip()
    vp = avatar_profile.get("voice_profile") or {}
    if isinstance(vp, dict):
        sig = sig or (vp.get("opening_phrase") or "").strip()
    if sig:
        parts.append(f"Opening/signature phrase (use exactly): {sig}")
    persona = avatar_profile.get("persona") or {}
    if isinstance(persona, dict) and persona:
        parts.append(f"Persona: tone={persona.get('tone')}, style={persona.get('style')}, hook_style={persona.get('hook_style')}")
    parts.append("--- End avatar identity ---")
    snap = _avatar_profiles_yaml_snapshot()
    if snap:
        parts.append("")
        parts.append("--- FULL AVATAR CATALOG (YAML, authoritative; do not invent IDs not listed) ---")
        parts.append(snap[:12000])
        parts.append("--- End catalog ---")
    return "\n".join(parts)


def _llm_chat(messages: list, model: Optional[str] = None) -> Optional[str]:
    """Call GROQ or OpenAI; return content or None. Retries up to 3 times on transient failure."""
    import time as _time

    _chat_fn = None
    try:
        from services.ai.groq_client import _chat as _chat_fn, groq_key_configured

        groq_ok = bool(groq_key_configured())
    except Exception:
        groq_ok = bool((os.environ.get("GROQ_API_KEY") or "").strip())

    api_key_openai = (os.environ.get("OPENAI_API_KEY") or "").strip()

    for attempt in range(3):
        if groq_ok and _chat_fn:
            try:
                out = _chat_fn(messages, model=model)
                if out:
                    return out
            except Exception:
                pass
            try:
                import sys as _sys

                _repo = os.path.dirname(
                    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                )
                if _repo not in _sys.path:
                    _sys.path.insert(0, _repo)
                from services.ai.groq_client import _chat as _chat2

                out = _chat2(messages, model=model)
                if out:
                    return out
            except Exception:
                pass
        if api_key_openai:
            try:
                url = "https://api.openai.com/v1/chat/completions"
                data = json.dumps({
                    "model": model or "gpt-4o-mini",
                    "messages": messages,
                }).encode("utf-8")
                req = urllib.request.Request(
                    url,
                    data=data,
                    headers={
                        "Authorization": f"Bearer {api_key_openai}",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=60) as resp:
                    out = json.loads(resp.read().decode())
                choice = (out.get("choices") or [None])[0]
                if choice:
                    return (choice.get("message") or {}).get("content")
            except Exception:
                pass
        if attempt < 2:
            _time.sleep(2)
    return None


def _parse_script_json(raw: str) -> Optional[Dict[str, Any]]:
    """Try to parse LLM response as JSON matching strict schema. Returns dict or None."""
    if not raw or not raw.strip():
        return None
    raw = raw.strip()
    # Extract JSON from markdown code block if present
    m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw)
    if m:
        raw = m.group(1)
    # Find first { ... } span
    start = raw.find("{")
    if start == -1:
        return None
    depth = 0
    end = -1
    for i in range(start, len(raw)):
        if raw[i] == "{":
            depth += 1
        elif raw[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end == -1:
        return None
    try:
        data = json.loads(raw[start:end])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    try:
        from services.influencer_engine.models.script_schemas import ScriptJsonOutput

        data = ScriptJsonOutput.model_validate(data).model_dump()
    except Exception:
        return None
    for key in SCRIPT_JSON_KEYS:
        if key not in data or not isinstance(data.get(key), str):
            return None
    return data


def _parse_script_response(raw: str) -> Dict[str, Any]:
    """Parse HOOK/MAIN/CTA/HASHTAGS from LLM response."""
    out = {
        "hook": "",
        "main_content": "",
        "cta": "",
        "hashtags": "",
        "full_text": raw.strip(),
    }
    if not raw:
        return out
    raw = raw.strip()
    sections = ["HOOK:", "MAIN:", "CTA:", "HASHTAGS:"]
    for i, sec in enumerate(sections):
        if sec not in raw:
            continue
        start = raw.find(sec) + len(sec)
        end = raw.find(sections[i + 1], start) if i + 1 < len(sections) else len(raw)
        block = raw[start:end].strip()
        key = "hook" if sec == "HOOK:" else "main_content" if sec == "MAIN:" else "cta" if sec == "CTA:" else "hashtags"
        out[key] = block
    # Build single narration for TTS
    parts = [out["hook"], out["main_content"], out["cta"]]
    out["narration"] = " ".join(p for p in parts if p).replace("\n", " ").strip()
    return out


def _ensure_opening_phrase(parsed: Dict[str, Any], opening_phrase: str) -> None:
    """Ensure narration and full_text start with the avatar's opening phrase (e.g. Vanakkam Makkalae,)."""
    if not opening_phrase or not opening_phrase.strip():
        return
    prefix = (opening_phrase.strip() + ", ").strip()
    narration = (parsed.get("narration") or "").strip()
    if narration and not narration.lower().startswith(prefix.lower().rstrip(",").strip().lower()):
        parsed["narration"] = prefix + " " + narration.lstrip()
    full = (parsed.get("full_text") or "").strip()
    if full and not full.lower().startswith(prefix.lower().rstrip(",").strip().lower()):
        parsed["full_text"] = prefix + "\n\n" + full.lstrip()


def generate_script_with_llm(
    topic: str,
    persona: str = "",
    hook: str = "",
    avatar_profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Generate script using LLM (getlate/GROQ/OpenAI from .env).
    If avatar_profile is provided, injects avatar tone and signature/opening phrase.
    Returns dict: hook, main_content, cta, hashtags, narration, full_text, mock (bool).
    On API failure, falls back to mock script.
    """
    persona = persona or "engaging creator"
    hook = hook or "You won't believe this."
    opening_phrase = ""
    signature_instruction = ""
    if avatar_profile:
        opening_phrase = (avatar_profile.get("signature_phrase") or "").strip()
        vp = avatar_profile.get("voice_profile") or {}
        if isinstance(vp, dict):
            opening_phrase = opening_phrase or (vp.get("opening_phrase") or "").strip()
        tone = (avatar_profile.get("persona") or {})
        if isinstance(tone, dict):
            persona = (tone.get("tone") or persona).strip() or persona
        if opening_phrase:
            signature_instruction = f"The script MUST start with this exact opening line: \"{opening_phrase},\" then continue with the hook and main content."
    if not signature_instruction:
        signature_instruction = ""
    try:
        from core.services.geo_targeting import build_audience_instruction

        geo_line = build_audience_instruction()
    except Exception:
        geo_line = ""
    prompt = SCRIPT_PROMPT_TEMPLATE.format(
        topic=topic,
        persona=persona,
        hook=hook,
        signature_instruction=signature_instruction,
    )
    if geo_line:
        prompt += f"\n\nAudience targeting: {geo_line}"
    try:
        from klipaura_core.infrastructure.venture_lab_store import top_hooks_context_block

        th = top_hooks_context_block()
        if th:
            prompt += f"\n\n{th}"
    except Exception:
        pass
    system_content = _build_avatar_identity_system_prompt(avatar_profile)
    content = _llm_chat([
        {"role": "system", "content": system_content},
        {"role": "user", "content": prompt},
    ])
    if content:
        parsed = _parse_script_json(content)
        if parsed:
            narration = " ".join([
                (parsed.get("hook") or "").strip(),
                (parsed.get("main_content") or "").strip(),
                (parsed.get("cta") or "").strip(),
            ]).replace("\n", " ").strip()
            parsed["narration"] = narration
            parsed["full_text"] = content.strip()
        else:
            parsed = _parse_script_response(content)
        _ensure_opening_phrase(parsed, opening_phrase)
        parsed["mock"] = False
        if parsed.get("compliance_pass") is False:
            try:
                from shared.compliance_abort import log_compliance_abort

                log_compliance_abort(
                    (topic or "")[:40],
                    str(parsed.get("compliance_reason") or ""),
                    source="script_agent",
                )
            except Exception:
                pass
            return {
                "hook": "",
                "main_content": "",
                "cta": "",
                "hashtags": "",
                "narration": "",
                "full_text": str(parsed.get("compliance_reason") or "Compliance review failed."),
                "mock": False,
                "compliance_blocked": True,
                "compliance_pass": False,
                "compliance_reason": str(parsed.get("compliance_reason") or ""),
            }
        combined = " ".join(
            str(parsed.get(k) or "")
            for k in ("hook", "main_content", "cta", "hashtags", "narration")
        )
        try:
            from shared.uae_content_safety import assess_uae_media_compliance, format_safety_block
            br = assess_uae_media_compliance(topic or "", combined)
            if br:
                return {
                    "hook": "",
                    "main_content": "",
                    "cta": "",
                    "hashtags": "",
                    "narration": format_safety_block(br),
                    "full_text": format_safety_block(br),
                    "mock": False,
                    "uae_blocked": True,
                }
        except Exception:
            pass
        try:
            from services.ai.groq_client import append_mandatory_affiliate_disclosure_script_parts

            parsed = append_mandatory_affiliate_disclosure_script_parts(parsed)
            parsed["narration"] = " ".join(
                [
                    (parsed.get("hook") or "").strip(),
                    (parsed.get("main_content") or "").strip(),
                    (parsed.get("cta") or "").strip(),
                ]
            ).replace("\n", " ").strip()
        except Exception:
            pass
        return parsed
    out = _mock_script(topic, persona, hook, opening_phrase)
    _ensure_opening_phrase(out, opening_phrase)
    try:
        from shared.uae_content_safety import assess_uae_media_compliance, format_safety_block
        mc = " ".join(str(out.get(k) or "") for k in ("hook", "main_content", "cta", "hashtags", "narration"))
        br = assess_uae_media_compliance(topic or "", mc)
        if br:
            out["narration"] = format_safety_block(br)
            out["full_text"] = format_safety_block(br)
            out["uae_blocked"] = True
    except Exception:
        pass
    return out


def _mock_script(topic: str, persona: str, hook: str, opening_phrase: str = "") -> Dict[str, Any]:
    """Fallback mock script when LLM is unavailable."""
    lead = (opening_phrase.strip() + ", ") if opening_phrase else ""
    narration = f"{lead}{hook or ''} Today we're talking about {topic}. Like and follow for more.".strip()
    return {
        "hook": hook or f"Here's why {topic} matters.",
        "main_content": f"Today we're talking about {topic}. This is something you need to know. Keep watching for the key takeaway.",
        "cta": "Like and follow for more. Link in bio.",
        "hashtags": "viral,trending,shortform,content",
        "narration": narration,
        "full_text": "",
        "mock": True,
        "compliance_pass": True,
        "compliance_reason": "",
    }


class ScriptAgent:
    """Agent for generating and validating scripts. Supports avatar tone and signature phrase."""

    def generate(
        self,
        topic: str,
        persona: str = "",
        hook: str = "",
        use_llm: bool = True,
        avatar_profile: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Generate script. If use_llm=True, uses LLM with mock fallback.
        If avatar_profile is provided, injects signature phrase and persona tone.
        Returns same structure as generate_script_with_llm.
        """
        opening = ""
        if avatar_profile:
            opening = (avatar_profile.get("signature_phrase") or "").strip()
            vp = avatar_profile.get("voice_profile") or {}
            if isinstance(vp, dict):
                opening = opening or (vp.get("opening_phrase") or "").strip()
        if use_llm:
            return generate_script_with_llm(topic, persona, hook, avatar_profile)
        return _mock_script(topic, persona, hook, opening)
