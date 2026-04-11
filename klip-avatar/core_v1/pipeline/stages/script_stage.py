import os
import re
import requests
from utils.logger import log_stage, log_error, log_structured
from utils.state import state

import pipeline.job_helpers  # noqa: F401 — ensures core_v1 on sys.path
from engine.cinematic_v2.video_config import resolve_duration_seconds


def _fallback_script_from_topic(topic: str) -> str:
    """Dynamic script when Groq is unavailable or errors (not a single fixed template)."""
    safe = (topic or "this").strip()
    if not safe:
        safe = "this topic"
    snippet = safe[:160]
    words = re.findall(r"\w+", safe.lower())
    focus = words[0] if words else "this"
    return (
        f"Hook: Quick take on {snippet} — here's what actually matters.\n\n"
        f"Body: In plain terms: {focus} is worth a look because it saves time and cuts the noise. "
        f"I am sharing what I would check first before you spend a cent.\n\n"
        f"CTA: Save this, follow for more, and tap the link in bio for the full breakdown."
    )


def _affiliate_script(product_title: str) -> str:
    t = (product_title or "This product").strip()[:200]
    return (
        "This product is going viral right now...\n\n"
        f"{t}\n\n"
        "People are buying this because...\n\n"
        "Get it before it sells out.\n\n"
        "Link in bio."
    )


def _trim_script_to_budget(text: str, target_sec: float) -> str:
    """Rough TTS budget: ~2.5 words/sec for short-form."""
    words = (text or "").split()
    max_words = max(12, int(float(target_sec) * 2.5))
    if len(words) <= max_words:
        return (text or "").strip()
    return " ".join(words[:max_words]).strip()


def run(job: dict) -> str:
    """Generate script from topic mode or affiliate product context."""
    topic = (job.get("topic") or "").strip()
    product_url = (job.get("product_url") or "").strip()
    mode = (job.get("mode") or "TOPIC").upper()
    vc = job.get("video_config") or {}
    duration_tier = str(vc.get("duration_tier") or "SHORT").upper()
    try:
        target_sec = resolve_duration_seconds(duration_tier)
    except Exception:
        target_sec = 6.0

    log_stage("SCRIPT", f"Generating script (mode={mode})", 20)
    state.update(current_topic=topic or product_url, pipeline_mode=mode)

    if product_url and mode == "AFFILIATE":
        try:
            from engine.product_extractor import extract_product_data

            data = extract_product_data(product_url)
            title = (data.get("title") or "").strip() or "This product"
            script = _affiliate_script(title)
            script = _trim_script_to_budget(script, target_sec)
            log_structured("script", "Affiliate script from product URL", "info", path="real", ok=data.get("ok"))
            log_stage("SCRIPT", "Affiliate script ready", 30)
            return script
        except Exception as e:
            log_error("SCRIPT", str(e))
            log_structured("script", "Product extract failed — fallback topic flow", "warn", error=str(e)[:200])
            mode = "TOPIC"

    if not topic and mode == "TOPIC":
        topic = "short-form recommendation"

    groq_key = (os.getenv("GROQ_API_KEY", "") or "").strip()
    model = (os.getenv("GROQ_MODEL") or os.getenv("GROQ_CHAT_MODEL") or "llama-3.3-70b-versatile").strip()

    try:
        if groq_key and len(groq_key) > 10:
            log_structured("script", "Calling Groq for AI script", "info", path="real", model=model)

            url = "https://api.groq.com/openai/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {groq_key}",
                "Content-Type": "application/json",
            }
            messages = [
                {"role": "system", "content": "You are a short-form video script writer."},
                {"role": "user", "content": topic},
            ]
            payload = {
                "model": model,
                "messages": messages,
                "temperature": 0.7,
                "max_tokens": 500,
            }

            response = requests.post(url, json=payload, headers=headers, timeout=60)
            if response.status_code == 200:
                content = response.json()["choices"][0]["message"]["content"]
                log_structured("script", "Groq script OK", "info", path="real", model=model, chars=len(content or ""))
                log_stage("SCRIPT", "AI script generated successfully via Groq", 30)
                return _trim_script_to_budget(content, target_sec)
            else:
                log_structured(
                    "script",
                    "Groq API error — using dynamic fallback",
                    "warn",
                    path="fallback",
                    status=response.status_code,
                    response_body=(response.text or "")[:800],
                    request_payload={"model": payload["model"], "messages": payload["messages"]},
                )
                log_error("SCRIPT", f"Groq API error: {response.status_code}")
        else:
            log_structured("script", "No Groq key — dynamic fallback", "warn", path="fallback")

        script = _fallback_script_from_topic(topic)
        script = _trim_script_to_budget(script, target_sec)
        log_structured("script", "Script from fallback generator", "info", path="fallback", chars=len(script))
        log_stage("SCRIPT", "Script generated using fallback (Groq unavailable or error)", 30)
        return script

    except Exception as e:
        log_error("SCRIPT", str(e))
        log_structured("script", "Groq exception — dynamic fallback", "warn", path="fallback", error=str(e)[:300])
        script = _fallback_script_from_topic(topic)
        return _trim_script_to_budget(script, target_sec)
