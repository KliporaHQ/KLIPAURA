"""
Groq API client — topic, script, scenes, metadata. Standardized on api.groq.com.
Max retries: 3.
"""

from __future__ import annotations

import json
import logging
import os
import re
import typing as t

log = logging.getLogger(__name__)

# Legal Team system prompt (UAE Media Laws + platform policies) — paired with JSON narration output.
# 7-point shield: NSFW, gambling, illegal drugs, weapons, gov-rumors (UAE), scams, religious disrespect.
GROQ_LEGAL_TEAM_SYSTEM = (
    "You are the Legal Team for KLIPAURA. Every script MUST pass this 7-point check before compliance_pass "
    "can be true: (1) No NSFW or adult sexual content. (2) No gambling promotion. (3) No illegal drugs or "
    "drug paraphernalia. (4) No weapons or violence glorification. (5) No government rumors or unsubstantiated "
    "claims about UAE government or officials (UAE Media Law). (6) No scams, fraud, or deceptive get-rich-quick "
    "claims. (7) No hate speech, targeted harassment, religious disrespect, or incitement. "
    "Also evaluate against major platform policies (YouTube, TikTok, Instagram). "
    "If ANY of the above applies, you MUST set compliance_pass to false. "
    "Use respectful neutral English. For affiliate or sponsored product angles, ensure disclosure can be added. "
    "You MUST reply with valid JSON only, no markdown fences, no extra text."
)

# Mandatory line appended server-side for product/affiliate scripts (not a substitute for platform disclosure UI).
MANDATORY_AFFILIATE_DISCLOSURE = (
    "Disclosure: This content may include affiliate or sponsored mentions; I may earn a commission at no extra "
    "cost to you. Opinions are my own. Check the description for links and details."
)


def _looks_product_or_affiliate(topic: str, script: str) -> bool:
    blob = f"{topic or ''} {script or ''}".lower()
    keys = (
        "affiliate",
        "sponsor",
        "promo code",
        "discount code",
        "link in bio",
        "buy now",
        "product",
        "deal",
        "offer",
        "checkout",
        "commission",
    )
    return any(k in blob for k in keys)


def append_mandatory_affiliate_disclosure_narration(narration_script: str, topic: str) -> str:
    """Append hardcoded affiliate disclosure when content is product/affiliate-oriented."""
    text = (narration_script or "").strip()
    if not text or not _looks_product_or_affiliate(topic, text):
        return text
    line = MANDATORY_AFFILIATE_DISCLOSURE.strip()
    if line in text:
        return text
    return f"{text}\n\n{line}"


def append_mandatory_affiliate_disclosure_script_parts(parsed: dict) -> dict:
    """Append disclosure to CTA for structured influencer script JSON when product-based."""
    if not isinstance(parsed, dict):
        return parsed
    combined = " ".join(str(parsed.get(k) or "") for k in ("hook", "main_content", "cta", "hashtags"))
    topic_guess = combined[:500]
    if not _looks_product_or_affiliate(topic_guess, combined):
        return parsed
    line = MANDATORY_AFFILIATE_DISCLOSURE.strip()
    cta = str(parsed.get("cta") or "").strip()
    if line in cta or line in combined:
        return parsed
    parsed = dict(parsed)
    parsed["cta"] = (cta + " " + line).strip() if cta else line
    parsed["affiliate_disclosure_appended"] = True
    return parsed

try:
    import requests
except ImportError:
    requests = None

try:
    from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_random_exponential
except ImportError:
    retry = None  # type: ignore[misc, assignment]
    retry_if_exception_type = None  # type: ignore[misc, assignment]
    stop_after_attempt = None  # type: ignore[misc, assignment]
    wait_random_exponential = None  # type: ignore[misc, assignment]

GROQ_BASE = "https://api.groq.com/openai/v1"


class GroqRateLimitError(Exception):
    """HTTP 429 from Groq — triggers exponential backoff + jitter via tenacity."""


def _post_groq_chat_with_retries(
    key: str,
    use_model: str,
    messages: t.List[t.Dict[str, str]],
) -> t.Optional[requests.Response]:
    """Single-model POST with tenacity on 429 (when tenacity is installed)."""
    if not requests:
        return None

    def _once() -> requests.Response:
        r = requests.post(
            f"{GROQ_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": use_model, "messages": messages},
            timeout=60,
        )
        if r.status_code == 429:
            raise GroqRateLimitError(r.text[:200] if r.text else "429")
        return r

    if retry and wait_random_exponential and stop_after_attempt and retry_if_exception_type:
        @retry(
            wait=wait_random_exponential(multiplier=1, min=2, max=90),
            stop=stop_after_attempt(8),
            retry=retry_if_exception_type(GroqRateLimitError),
            reraise=True,
        )
        def _wrapped() -> requests.Response:
            return _once()

        return _wrapped()
    return _once()
# llama-3.1-70b-versatile was decommissioned on GroqCloud — use 3.3 or set GROQ_MODEL.
MAX_RETRIES = 3

# Last failure from Groq HTTP (for Mission Control toasts). Cleared on successful completion text.
_LAST_GROQ_ERROR: t.Optional[str] = None


def get_last_groq_error() -> t.Optional[str]:
    return _LAST_GROQ_ERROR


def _set_last_groq_error(msg: t.Optional[str]) -> None:
    global _LAST_GROQ_ERROR
    _LAST_GROQ_ERROR = (msg or "").strip() or None


def _default_chat_model() -> str:
    return (
        os.environ.get("GROQ_MODEL")
        or os.environ.get("GROQ_CHAT_MODEL")
        or "llama-3.3-70b-versatile"
    ).strip()


def _model_try_order(explicit: t.Optional[str]) -> t.List[str]:
    """Primary first, then env GROQ_MODEL_FALLBACKS, then known-good GroqCloud ids."""
    raw = (os.environ.get("GROQ_MODEL_FALLBACKS") or "").strip()
    extra = [x.strip() for x in raw.split(",") if x.strip()]
    # Fast + widely available fallbacks if primary model errors (region, decommission, etc.)
    builtins = ["llama-3.1-8b-instant", "llama-3.3-70b-versatile", "mixtral-8x7b-32768"]
    seen: set[str] = set()
    out: t.List[str] = []
    for m in [explicit, _default_chat_model(), *extra, *builtins]:
        if not m:
            continue
        m = str(m).strip()
        if m and m not in seen:
            seen.add(m)
            out.append(m)
    return out


def _get_api_key() -> str:
    """Prefer Mission Control config (runtime), then env."""
    try:
        from core.services.config_service import get_secret
        key = get_secret("groq_api_key")
        if key:
            return key
    except Exception:
        pass
    return os.environ.get("GROQ_API_KEY") or ""


def groq_key_configured() -> bool:
    """True if Groq HTTP client can obtain an API key (Redis secret or GROQ_API_KEY)."""
    return bool((_get_api_key() or "").strip())


def _chat(messages: t.List[t.Dict[str, str]], model: t.Optional[str] = None) -> t.Optional[str]:
    """Chat completion; tries model fallback chain so one bad model id does not kill the pipeline."""
    if not requests:
        _set_last_groq_error("Python package `requests` is not installed")
        return None
    key = _get_api_key()
    if not key:
        _set_last_groq_error("No API key (set GROQ_API_KEY or Redis secret groq_api_key)")
        return None

    explicit = (model or "").strip() or None
    overall_last: t.Optional[str] = None

    for use_model in _model_try_order(explicit):
        last_err: t.Optional[str] = None
        for attempt in range(MAX_RETRIES):
            try:
                try:
                    r = _post_groq_chat_with_retries(key, use_model, messages)
                except GroqRateLimitError as gre:
                    last_err = f"[{use_model}] HTTP 429 after retries: {gre}"
                    log.warning("Groq rate limit exhausted: %s", last_err[:400])
                    overall_last = last_err or overall_last
                    break
                if r is None:
                    last_err = f"[{use_model}] no response"
                    continue
                if r.status_code != 200:
                    try:
                        body = r.text[:500] if r.text else ""
                    except Exception:
                        body = ""
                    last_err = f"[{use_model}] HTTP {r.status_code}: {body}"
                    continue
                try:
                    data = r.json()
                except Exception:
                    last_err = f"[{use_model}] non-JSON response"
                    continue
                choice = (data.get("choices") or [None])[0]
                if not choice:
                    err_obj = data.get("error")
                    last_err = f"[{use_model}] no choices — error={err_obj!r}"[:500]
                    continue
                txt = (choice.get("message") or {}).get("content")
                if txt is not None and str(txt).strip():
                    _set_last_groq_error(None)
                    log.debug("Groq ok model=%s chars=%s", use_model, len(str(txt)))
                    return str(txt).strip()
                last_err = f"[{use_model}] empty message.content"
            except Exception as exc:
                last_err = f"[{use_model}] {type(exc).__name__}: {exc}"
                log.warning("Groq attempt exception: %s", last_err[:400])
        overall_last = last_err or overall_last

    if overall_last:
        _set_last_groq_error(overall_last)
        log.warning("Groq chat failed all models: %s", overall_last[:450])
    else:
        _set_last_groq_error("unknown (no response)")
    return None


def generate_topic(genre: str = "Mystery", count: int = 1) -> t.List[str]:
    """Generate viral video topics. Returns list of topic strings."""
    content = _chat([
        {"role": "system", "content": "You suggest short, catchy topics for 30-second vertical viral videos. One topic per line, no numbering."},
        {"role": "user", "content": f"Genre: {genre}. Suggest {count} topic(s)."},
    ])
    if not content:
        return []
    lines = [ln.strip() for ln in content.strip().split("\n") if ln.strip()][:count]
    return lines or [content.strip()]


def generate_script(topic: str, genre: str = "Mystery", duration_sec: int = 30) -> t.Optional[str]:
    """Generate script for a short vertical video."""
    return _chat([
        {"role": "system", "content": "You write short, punchy scripts for faceless vertical videos. One paragraph, hook in first line. No stage directions."},
        {"role": "user", "content": f"Topic: {topic}. Genre: {genre}. Length: ~{duration_sec} seconds when read aloud."},
    ])


def generate_narration_script(topic: str, extra_instructions: str = "") -> t.Optional[str]:
    """
    Write a short-form video narration script (120-200 words) for the given topic.
    Style: engaging, concise, suitable for AI-generated voice narration.
    Returns only the narration script text, or None on failure.

    ``extra_instructions`` is appended to the user message (e.g. geo targeting from automation settings).
    """
    out = generate_narration_script_structured(topic, extra_instructions=extra_instructions)
    if not out:
        return None
    if out.get("compliance_pass") is False:
        return None
    return (out.get("narration_script") or "").strip() or None


def _extract_json_object(raw: str) -> t.Optional[str]:
    if not raw or not str(raw).strip():
        return None
    raw = str(raw).strip()
    m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", raw)
    if m:
        return m.group(1)
    start = raw.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(raw)):
        if raw[i] == "{":
            depth += 1
        elif raw[i] == "}":
            depth -= 1
            if depth == 0:
                return raw[start : i + 1]
    return None


def generate_narration_script_structured(topic: str, extra_instructions: str = "") -> t.Optional[t.Dict[str, t.Any]]:
    """
    Legal Team + JSON: narration_script, compliance_pass, compliance_reason.
    Returns dict or None on parse/API failure.
    """
    user = (
        f"Topic: {topic}\n\n"
        "Write a short-form video narration script (120-200 words). Style: engaging, concise, suitable for AI voice. "
        "If the topic involves promoting a product, deal, or affiliate link, you must still pass the 7-point legal check; "
        "the system will append a standard affiliate disclosure line automatically — do not refuse solely for disclosure. "
        'Return exactly this JSON shape: {"narration_script": "<full script text>", "compliance_pass": true or false, '
        '"compliance_reason": "<short reason>"}'
    )
    extra = (extra_instructions or "").strip()
    if extra:
        user += f"\n\nAdditional instructions:\n{extra}"
    raw = _chat(
        [
            {"role": "system", "content": GROQ_LEGAL_TEAM_SYSTEM},
            {"role": "user", "content": user},
        ]
    )
    if not raw:
        return None
    blob = _extract_json_object(raw)
    if not blob:
        return None
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    try:
        from services.influencer_engine.models.script_schemas import NarrationComplianceOutput

        compliance_ok = bool(data.get("compliance_pass", True))
        narration_script = str(data.get("narration_script") or "")
        if not compliance_ok:
            log.warning(
                "Groq Legal Team: compliance_pass=false reason=%s",
                str(data.get("compliance_reason") or "")[:500],
            )
            m = NarrationComplianceOutput.model_validate(
                {
                    "narration_script": narration_script,
                    "compliance_pass": False,
                    "compliance_reason": str(data.get("compliance_reason") or ""),
                }
            )
            return m.model_dump()
        narration_script = append_mandatory_affiliate_disclosure_narration(narration_script, topic)
        m = NarrationComplianceOutput.model_validate(
            {
                "narration_script": narration_script,
                "compliance_pass": True,
                "compliance_reason": str(data.get("compliance_reason") or ""),
            }
        )
        return m.model_dump()
    except Exception:
        return None


def generate_scenes(script: str, count: int = 5) -> t.List[str]:
    """Break script into scene descriptions (for image generation)."""
    content = _chat([
        {"role": "system", "content": "You break a video script into visual scene descriptions. One short line per scene, describing the image. No dialogue."},
        {"role": "user", "content": f"Script:\n{script}\n\nGive exactly {count} scene descriptions, one per line."},
    ])
    if not content:
        return []
    return [ln.strip() for ln in content.strip().split("\n") if ln.strip()][:count]


def generate_metadata(topic: str, script: str) -> t.Dict[str, str]:
    """Generate title, description, hashtags for publishing."""
    content = _chat([
        {"role": "system", "content": "You output: title (one line), description (2-3 sentences), hashtags (comma-separated). Format: TITLE: ... DESCRIPTION: ... HASHTAGS: ..."},
        {"role": "user", "content": f"Topic: {topic}\n\nScript excerpt: {script[:500]}"},
    ])
    out = {"title": topic, "description": "", "hashtags": ""}
    if not content:
        return out
    for part in content.split("HASHTAGS:"):
        if "TITLE:" in part:
            t_part = part.split("DESCRIPTION:")[0].replace("TITLE:", "").strip()
            out["title"] = t_part or out["title"]
        if "DESCRIPTION:" in part:
            d_part = part.split("HASHTAGS:")[0].replace("DESCRIPTION:", "").strip()
            out["description"] = d_part
        if "HASHTAGS:" in part or "hashtags" in part.lower():
            h = part.split(":")[-1].strip() if ":" in part else part.strip()
            out["hashtags"] = h
    return out


class GroqClient:
    """Reusable client with optional custom key and model."""

    def __init__(self, api_key: t.Optional[str] = None, model: t.Optional[str] = None):
        self.api_key = api_key or _get_api_key()
        self.model = (model or _default_chat_model()).strip()

    def topic(self, genre: str = "Mystery", count: int = 1) -> t.List[str]:
        return generate_topic(genre, count)

    def script(self, topic: str, genre: str = "Mystery", duration_sec: int = 30) -> t.Optional[str]:
        return generate_script(topic, genre, duration_sec)

    def scenes(self, script: str, count: int = 5) -> t.List[str]:
        return generate_scenes(script, count)

    def metadata(self, topic: str, script: str) -> t.Dict[str, str]:
        return generate_metadata(topic, script)
