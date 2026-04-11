"""
Video planning agent — adapts B-roll to content length and clip duration.

The agent's job: given narration and actual content duration (e.g. from TTS),
decide how many clips to generate, duration per clip (within Wavespeed limits),
split the script into segments, and produce motion prompts so the video
flows consistently. All decisions are content-length and API-aware.
"""

from __future__ import annotations

import os
import math
from typing import Any, Dict, List, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# Wavespeed I2V often supports 5s; some models support 10s. Agent uses this as max.
DEFAULT_MAX_CLIP_SEC = 5
MIN_CLIPS = 2
MAX_CLIPS = 12


def _estimate_duration_from_text(narration: str) -> float:
    """Rough duration in seconds from word count (~150 words/min speech)."""
    if not narration or not narration.strip():
        return 30.0
    words = len(narration.split())
    return max(10.0, min(120.0, words / 2.5))


def _split_into_segments(narration: str, num_segments: int) -> List[str]:
    """Split narration into num_segments, preferring sentence boundaries."""
    text = (narration or "").strip()
    if not text:
        return ["speaking to camera"] * num_segments
    sentences = [s.strip() for s in text.replace("!", ".").replace("?", ".").split(".") if s.strip()]
    if not sentences:
        sentences = [text]
    if len(sentences) <= num_segments:
        out = sentences + [""] * max(0, num_segments - len(sentences))
        return out[:num_segments]
    per = len(sentences) // num_segments
    extra = len(sentences) % num_segments
    segments = []
    idx = 0
    for i in range(num_segments):
        n = per + (1 if i < extra else 0)
        chunk = " ".join(sentences[idx : idx + n])
        idx += n
        segments.append(chunk or " ")
    return segments


def _motion_prompts_via_llm(segment_texts: List[str]) -> Optional[List[str]]:
    """Use Groq/LLM to generate one short motion prompt per segment (content-aware). Returns None on failure."""
    if not segment_texts:
        return None
    try:
        import sys as _sys

        _repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        if _repo not in _sys.path:
            _sys.path.insert(0, _repo)
        from services.ai.groq_client import _chat, _default_chat_model, groq_key_configured
    except Exception:
        return None
    if not groq_key_configured():
        return None
    parts = "\n".join([f"Segment {i+1}: {t[:200]}" for i, t in enumerate(segment_texts)])
    prompt = f"""You are a video director. For each script segment below, output ONE short motion prompt for an AI avatar (image-to-video): describe only the motion/expression, e.g. "speaking to camera, slight nod", "thoughtful pause, gentle smile". Keep each under 12 words. Output exactly {len(segment_texts)} lines, one per segment, no numbering or labels."""
    messages = [
        {"role": "user", "content": f"{prompt}\n\n{parts}"},
    ]
    try:
        out = _chat(messages, model=_default_chat_model())
        if not out:
            return None
        lines = [ln.strip() for ln in out.strip().split("\n") if ln.strip()][: len(segment_texts)]
        if len(lines) >= len(segment_texts):
            return lines[: len(segment_texts)]
    except Exception:
        pass
    return None


def _default_motion_prompts(n: int) -> List[str]:
    """Fallback motion prompts when LLM is not used or fails."""
    defaults = [
        "speaking to camera, gentle expression, subtle head movement",
        "nodding thoughtfully, slight smile, natural gesture",
        "emphasizing a point, confident expression, calm movement",
        "warm expression, speaking clearly, minimal motion",
        "thoughtful pause, soft expression, slight tilt",
        "closing thought, friendly expression, steady gaze",
    ]
    return [defaults[i % len(defaults)] for i in range(n)]


def plan_video(
    narration: str,
    content_duration_sec: Optional[float] = None,
    max_clip_duration_sec: Optional[int] = None,
    use_llm_motion_prompts: bool = True,
) -> Dict[str, Any]:
    """
    Agent: plan B-roll to match content length and API constraints.

    - content_duration_sec: actual duration (e.g. from TTS). If None, estimated from narration.
    - max_clip_duration_sec: max duration per I2V clip (Wavespeed limit, e.g. 5). From env WAVESPEED_I2V_MAX_CLIP_SEC or default 5.
    - use_llm_motion_prompts: if True and Groq available, generate content-aware motion prompts per segment.

    Returns:
        {
            "num_clips": int,
            "clip_duration_sec": int,
            "segment_texts": List[str],
            "motion_prompts": List[str],
            "content_duration_sec": float,
        }
    """
    duration = content_duration_sec
    if duration is None or duration <= 0:
        duration = _estimate_duration_from_text(narration)
    max_clip = max_clip_duration_sec
    if max_clip is None or max_clip <= 0:
        try:
            max_clip = int(os.environ.get("WAVESPEED_I2V_MAX_CLIP_SEC", "").strip() or "0") or DEFAULT_MAX_CLIP_SEC
        except Exception:
            max_clip = DEFAULT_MAX_CLIP_SEC
    max_clip = max(1, min(15, max_clip))

    num_clips = max(MIN_CLIPS, min(MAX_CLIPS, math.ceil(duration / max_clip)))
    # Per-clip duration: either max_clip or divide evenly so total >= duration
    clip_duration_sec = max(1, min(max_clip, math.ceil(duration / num_clips)))

    segment_texts = _split_into_segments(narration, num_clips)
    motion_prompts = None
    if use_llm_motion_prompts:
        motion_prompts = _motion_prompts_via_llm(segment_texts)
    if not motion_prompts or len(motion_prompts) != len(segment_texts):
        motion_prompts = _default_motion_prompts(len(segment_texts))

    return {
        "num_clips": num_clips,
        "clip_duration_sec": clip_duration_sec,
        "segment_texts": segment_texts,
        "motion_prompts": motion_prompts,
        "content_duration_sec": duration,
    }
