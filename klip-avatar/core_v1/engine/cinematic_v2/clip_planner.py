"""Turn scene list into per-clip prompts and durations (5–8s per scene aggregate)."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from .retention_engine import rerank_search_queries, sample_clip_duration, semantic_match_score

# Stock search queries: drop generic b-roll terms (hard filter before scoring)
GENERIC_TERMS = {"business", "people", "city", "office", "team"}


def is_generic(query: str) -> bool:
    q = (query or "").lower()
    return any(g in q for g in GENERIC_TERMS)


def _keywords(text: str, max_k: int = 5) -> list[str]:
    stop = {
        "the",
        "a",
        "an",
        "and",
        "or",
        "to",
        "of",
        "in",
        "on",
        "for",
        "is",
        "it",
        "this",
        "that",
        "your",
        "you",
        "with",
        "are",
        "as",
        "at",
        "be",
    }
    words = re.findall(r"[A-Za-z][A-Za-z0-9\-']+", (text or "").lower())
    out: list[str] = []
    for w in words:
        if len(w) < 3 or w in stop:
            continue
        if w not in out:
            out.append(w)
        if len(out) >= max_k:
            break
    return out or ["content"]


def _emotion_to_visual_style(emotion: str) -> str:
    em = (emotion or "").lower()
    mapping = {
        "urgency": "fast_paced_news_alert",
        "excitement": "uplifting_celebration",
        "curiosity": "mystery_reveal_soft_light",
    }
    return mapping.get(emotion, "lifestyle_vertical_b_roll")


def _search_queries_from_scene(scene: dict[str, Any], chunk: str) -> list[str]:
    base_kw = _keywords(chunk)
    em = str(scene.get("emotion") or "").lower()
    extra: list[str] = []
    if em == "urgency":
        extra = ["fast", "breaking", "alert", "city night"]
    elif em == "excitement":
        extra = ["celebration", "bright", "energy"]
    elif em == "curiosity":
        extra = ["question", "discovery", "close up"]
    merged = base_kw + extra
    out: list[str] = []
    for x in merged:
        if x not in out:
            out.append(x)
    filtered = [q for q in out if not is_generic(q)]
    if not filtered:
        filtered = list(out)
    return filtered[:8]


def _visual_intent(scene_type: str, scene: dict[str, Any] | None = None) -> str:
    t = (scene_type or "").lower()
    if scene and str(scene.get("visual_style") or "") == "fast-cut":
        return "bold_opener_high_energy"
    if scene:
        return _emotion_to_visual_style(str(scene.get("emotion") or ""))
    if t == "hook":
        return "bold_opener_high_energy"
    if t == "cta":
        return "clear_call_to_action"
    return "supporting_detail_b_roll"


def _motion_type(scene_type: str, clip_index: int) -> str:
    base = (scene_type or "point").lower()
    variants = [
        "slow_zoom_in",
        "slow_zoom_out",
        "pan_left_soft",
        "pan_right_soft",
        "ken_burns_center",
    ]
    h = int(hashlib.md5(f"{base}:{clip_index}".encode(), usedforsecurity=False).hexdigest(), 16)
    return variants[h % len(variants)]


def _clip_count_for_scene(scene: dict[str, Any], target_duration_sec: float | None) -> int:
    """Fewer clips per scene when global duration budget is short."""
    if target_duration_sec is not None:
        if target_duration_sec <= 8:
            return 1
        if target_duration_sec <= 14:
            text = str(scene.get("text") or "")
            return 1 if len(text.split()) <= 28 else 2
    text = str(scene.get("text") or "")
    n = len(text.split())
    if n <= 18:
        return 1
    if n <= 48:
        return 2
    return 3


def _duration_for_clip(seed: str, idx: int) -> float:
    h = int(hashlib.md5(f"{seed}:{idx}".encode(), usedforsecurity=False).hexdigest(), 16)
    return 5.0 + (h % 31) / 10.0  # 5.0..8.0


def generate_clip_plan(
    scenes: list,
    target_duration_sec: float | None = None,
) -> list[dict[str, Any]]:
    """
    Each scene produces 1–3 clips. Default durations are 5–8s per clip (legacy).

    When ``target_duration_sec`` is set, clip counts and base durations are tightened so the
    stitched timeline can align with the global voice duration budget.

    When scenes include rel_retention_layer=True (from retention_engine.enhance_scenes),
    durations follow REL pacing (hook opener 1.2–2.5s; intensity-based ranges) and
    clips gain semantic_match_score plus optional caption/retention fields.

    Output shape:
    [
      {
        "scene_type": "hook",
        "clips": [{"prompt": "...", "duration": 6, "keywords": [...], "visual_intent": "...", "motion_type": "..."}]
      },
      ...
    ]
    """
    out: list[dict[str, Any]] = []
    scene_index = 0
    for si, scene in enumerate(scenes or []):
        if not isinstance(scene, dict):
            continue
        st = str(scene.get("type") or "point")
        text = str(scene.get("text") or "").strip()
        if not text:
            continue
        n_clips = _clip_count_for_scene(scene, target_duration_sec)
        words = text.split()
        chunks: list[str] = []
        if n_clips == 1:
            chunks = [text]
        else:
            step = max(1, len(words) // n_clips)
            for i in range(n_clips):
                start = i * step
                end = len(words) if i == n_clips - 1 else min(len(words), (i + 1) * step)
                chunk = " ".join(words[start:end]).strip()
                if chunk:
                    chunks.append(chunk)
        if not chunks:
            chunks = [text]
        clips: list[dict[str, Any]] = []
        scene_dict = scene if isinstance(scene, dict) else {}
        use_rel = bool(scene_dict.get("rel_retention_layer"))
        for j, chunk in enumerate(chunks):
            seed = f"{st}:{j}:{chunk[:40]}"
            if use_rel:
                rel_d = sample_clip_duration(scene_dict, scene_index, j, chunk)
                dur = rel_d if rel_d > 0 else max(5.0, min(8.0, round(_duration_for_clip(seed, j), 2)))
                if target_duration_sec is not None:
                    cap = max(2.0, min(8.0, target_duration_sec / max(1, n_clips * max(1, len(scenes or [])))))
                    dur = min(dur, cap)
            else:
                dur = round(_duration_for_clip(seed, j), 2)
                dur = max(5.0, min(8.0, dur))
                if target_duration_sec is not None:
                    cap = max(2.0, min(8.0, target_duration_sec / max(1, n_clips * max(1, len(scenes or [])))))
                    dur = min(dur, cap)
            kw = _keywords(chunk)
            sq = _search_queries_from_scene(scene_dict, chunk)
            if use_rel:
                sq = rerank_search_queries(chunk, scene_dict, sq)
            sms = semantic_match_score(chunk, scene_dict)
            clip_dict: dict[str, Any] = {
                "prompt": f"Cinematic vertical 9:16 shot: {chunk}",
                "duration": dur,
                "keywords": kw,
                "search_queries": sq,
                "visual_intent": _visual_intent(st, scene_dict),
                "motion_type": _motion_type(st, j),
                "emotion": str(scene_dict.get("emotion") or ""),
                "intensity": str(scene_dict.get("intensity") or "medium"),
                "visual_style": str(scene_dict.get("visual_style") or "smooth"),
                "scene_type": st,
                "semantic_match_score": round(sms, 4),
            }
            if st == "hook":
                clip_dict["lock_duration"] = True
            if use_rel:
                clip_dict["estimated_speech_duration"] = scene_dict.get("estimated_speech_duration")
                clip_dict["word_density"] = scene_dict.get("word_density")
                clip_dict["emphasis_points"] = list(scene_dict.get("emphasis_points") or [])
                clip_dict["retention_risk"] = scene_dict.get("retention_risk")
                clip_dict["engagement_type"] = scene_dict.get("engagement_type")
            clips.append(clip_dict)
        out.append({"scene_type": st, "scene_index": si, "clips": clips})
        scene_index += 1
    return out
