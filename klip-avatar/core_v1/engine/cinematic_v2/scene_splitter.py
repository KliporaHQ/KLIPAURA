"""Split narration script into hook / body points / CTA segments."""

from __future__ import annotations

import re
from typing import Any


def _infer_emotion(scene_type: str, text: str) -> str:
    t = (text or "").lower()
    st = (scene_type or "").lower()
    if st == "hook":
        if any(w in t for w in ("now", "stop", "hurry", "last", "breaking", "alert")):
            return "urgency"
        return "curiosity"
    if st == "cta":
        return "excitement"
    if any(w in t for w in ("secret", "why", "how")):
        return "curiosity"
    return "curiosity"


def _infer_intensity(scene_type: str, text: str) -> str:
    st = (scene_type or "").lower()
    if st == "hook":
        return "high"
    if st == "cta":
        return "high"
    n = len((text or "").split())
    if n <= 10:
        return "medium"
    return "medium"


def _infer_visual_style(scene_type: str, emotion: str, intensity: str) -> str:
    st = (scene_type or "").lower()
    em = (emotion or "").lower()
    if st == "hook" or intensity == "high":
        return "fast-cut"
    if em == "urgency":
        return "fast-cut"
    if em == "excitement" or st == "cta":
        return "dramatic"
    if intensity == "low":
        return "smooth"
    return "smooth"


def enrich_scene(scene: dict[str, Any]) -> dict[str, Any]:
    """Public: ensure scene dict has emotion / intensity / visual_style for clip + transition logic."""
    return _enrich_scene(dict(scene))


def _enrich_scene(scene: dict[str, Any]) -> dict[str, Any]:
    """Add emotion / intensity / visual_style for downstream clip + transition logic."""
    st = str(scene.get("type") or "point")
    text = str(scene.get("text") or "")
    em = str(scene.get("emotion") or "").strip() or _infer_emotion(st, text)
    it = str(scene.get("intensity") or "").strip() or _infer_intensity(st, text)
    vs = str(scene.get("visual_style") or "").strip() or _infer_visual_style(st, em, it)
    out = dict(scene)
    out["emotion"] = em
    out["intensity"] = it
    out["visual_style"] = vs
    return out


def _clean_block(s: str) -> str:
    t = (s or "").strip()
    t = re.sub(r"\s+", " ", t)
    return t


def _split_sentences(text: str) -> list[str]:
    if not text.strip():
        return []
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _max_scenes_for_target(target_duration_sec: float | None) -> int | None:
    """Fewer scenes for shorter global duration (voice + video budget)."""
    if target_duration_sec is None:
        return None
    if target_duration_sec <= 8:
        return 3
    if target_duration_sec <= 14:
        return 4
    if target_duration_sec <= 22:
        return 5
    return 6


def _collapse_scenes_for_budget(scenes: list[dict[str, Any]], max_scenes: int) -> list[dict[str, Any]]:
    if max_scenes <= 0 or len(scenes) <= max_scenes:
        return scenes
    hook = next((s for s in scenes if str(s.get("type")) == "hook"), None)
    cta = None
    for s in reversed(scenes):
        if str(s.get("type")) == "cta":
            cta = s
            break
    body_texts = [str(s.get("text") or "") for s in scenes if str(s.get("type")) == "point"]
    if not body_texts:
        body_texts = [
            str(s.get("text") or "")
            for s in scenes[1:-1]
            if s is not hook and s is not cta
        ]
    merged_body = _clean_block(" ".join(body_texts))
    out: list[dict[str, Any]] = []
    if hook:
        out.append(hook)
    elif scenes:
        out.append(_enrich_scene({"type": "hook", "text": _clean_block(str(scenes[0].get("text") or ""))}))
    if merged_body:
        out.append(_enrich_scene({"type": "point", "text": merged_body}))
    if cta:
        out.append(cta)
    else:
        out.append(_enrich_scene({"type": "cta", "text": "Tap the link — limited time."}))
    return out[:max_scenes] if len(out) > max_scenes else out


def split_script_into_scenes(
    script: str,
    target_duration_sec: float | None = None,
) -> list[dict[str, Any]]:
    """
    Split script into logical segments: hook, body points, CTA.

    When ``target_duration_sec`` is set (global duration budget), scene count is capped
    so clip planning stays coherent for SHORT/STANDARD/LONG tiers.

    Returns a JSON-serializable list:
    [{"type": "hook", "text": "..."}, {"type": "point", "text": "..."}, {"type": "cta", "text": "..."}]
    """
    raw = (script or "").strip()
    if not raw:
        return []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", raw) if p.strip()]
    if not paragraphs:
        paragraphs = [raw]

    scenes: list[dict[str, Any]] = []

    if len(paragraphs) == 1:
        sentences = _split_sentences(paragraphs[0])
        if len(sentences) <= 2:
            scenes.append(_enrich_scene({"type": "hook", "text": _clean_block(paragraphs[0])}))
            if len(sentences) == 2:
                scenes.append(_enrich_scene({"type": "cta", "text": _clean_block(sentences[1])}))
            cap = _max_scenes_for_target(target_duration_sec)
            if cap is not None and len(scenes) > cap:
                scenes = _collapse_scenes_for_budget(scenes, cap)
            return scenes
        hook = sentences[0]
        cta = sentences[-1]
        body_sents = sentences[1:-1]
        scenes.append(_enrich_scene({"type": "hook", "text": _clean_block(hook)}))
        if body_sents:
            mid = len(body_sents) // 2 or 1
            scenes.append(_enrich_scene({"type": "point", "text": _clean_block(" ".join(body_sents[:mid]))}))
            if len(body_sents) > mid:
                scenes.append(_enrich_scene({"type": "point", "text": _clean_block(" ".join(body_sents[mid:]))}))
        scenes.append(_enrich_scene({"type": "cta", "text": _clean_block(cta)}))
    else:
        hook = paragraphs[0]
        cta = paragraphs[-1] if len(paragraphs) > 1 else ""
        body_paras = paragraphs[1:-1] if len(paragraphs) > 2 else []

        scenes.append(_enrich_scene({"type": "hook", "text": _clean_block(hook)}))
        for bp in body_paras:
            for chunk in _split_sentences(bp) or [bp]:
                scenes.append(_enrich_scene({"type": "point", "text": _clean_block(chunk)}))
        if cta:
            scenes.append(_enrich_scene({"type": "cta", "text": _clean_block(cta)}))

        if not any(s["type"] == "cta" for s in scenes):
            scenes.append(_enrich_scene({"type": "cta", "text": "Tap the link — limited time."}))

    if not any(str(s.get("type")) == "cta" for s in scenes):
        scenes.append(_enrich_scene({"type": "cta", "text": "Tap the link — limited time."}))

    cap = _max_scenes_for_target(target_duration_sec)
    if cap is not None and len(scenes) > cap:
        scenes = _collapse_scenes_for_budget(scenes, cap)
    return scenes
