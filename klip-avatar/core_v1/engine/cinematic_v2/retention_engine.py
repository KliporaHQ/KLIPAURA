"""Retention Engine Layer (REL): TikTok/Reels-oriented scene and clip metadata."""

from __future__ import annotations

import hashlib
import math
import random
import re
from typing import Any

from .stitch_engine import TRANSITION_WEIGHTS, get_transition_filter, weighted_choice

# Hook opener: short, high-intensity window
HOOK_DURATION_MIN = 1.2
HOOK_DURATION_MAX = 2.5

# Pacing bounds by intensity (seconds per clip)
_PACING_HIGH = (0.8, 1.8)
_PACING_MEDIUM = (1.5, 3.0)
_PACING_LOW = (2.0, 4.0)

# Extended transition vocabulary (mapped in stitch_engine to xfade/ffmpeg-safe names)
REL_TRANSITION_EXTRAS = ("whip_pan", "glitch_cut", "blur_snap")

# Pool for diversity when intensity allows motion variety
_HIGH_ENERGY_POOL = (
    "slideleft",
    "slideright",
    "zoomin",
    "fade",
    "whip_pan",
    "glitch_cut",
    "blur_snap",
)


def _rng_for_scene(scene: dict[str, Any], salt: str) -> random.Random:
    key = f"{salt}:{scene.get('type')}:{str(scene.get('text', ''))[:120]}"
    h = int(hashlib.md5(key.encode(), usedforsecurity=False).hexdigest(), 16)
    return random.Random(h)


def _word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text or ""))


def _emphasis_points(text: str, max_n: int = 6) -> list[str]:
    t = text or ""
    out: list[str] = []
    for m in re.finditer(r"\b([A-Z][a-z]+)\b", t):
        w = m.group(1)
        if len(w) > 2 and w not in out:
            out.append(w)
    for w in re.findall(r"\b\w+\b", t.lower()):
        if len(w) < 4:
            continue
        if w.endswith("!!") or any(x in w for x in ("secret", "free", "now", "stop", "hack")):
            if w not in out:
                out.append(w)
        if len(out) >= max_n:
            break
    if not out:
        words = [x for x in re.findall(r"\b\w+\b", t.lower()) if len(x) > 3]
        out = words[:max_n]
    return out[:max_n]


def _infer_engagement_type(scene_type: str, emotion: str, text: str) -> str:
    st = (scene_type or "").lower()
    em = (emotion or "").lower()
    tl = (text or "").lower()
    if st == "hook" and any(w in tl for w in ("stop", "wait", "look", "watch")):
        return "shock"
    if em == "urgency":
        return "shock"
    if st == "cta":
        return "value"
    if "story" in tl or "when" in tl or "because" in tl:
        return "storytelling"
    if em == "curiosity" or "?" in tl:
        return "curiosity"
    return "value"


def _infer_retention_risk(scene_type: str, text: str, _word_density: float) -> str:
    t = (text or "").strip()
    wc = _word_count(t)
    st = (scene_type or "").lower()
    if st != "hook" and wc < 5:
        return "high"
    if wc > 55:
        return "medium"
    return "low"


def compute_hook_score(scene: dict[str, Any]) -> float:
    """Script-grounded hook strength (deterministic from scene text)."""
    text = str(scene.get("text", "")).lower()
    score = 0.0
    triggers = ["you", "this", "why", "stop", "never", "secret"]
    if any(t in text for t in triggers):
        score += 0.25
    if "!" in text:
        score += 0.15
    if "?" in text:
        score += 0.2
    word_count = len(text.split())
    if word_count <= 12:
        score += 0.2
    return min(score, 1.0)


def weighted_word_timings(text: str, total_duration: float) -> list[dict[str, Any]]:
    """Word-level timing with weight bias (punctuation, length) for natural caption pacing."""
    words = (text or "").split()
    if not words:
        return []
    weights: list[float] = []
    for w in words:
        if w.endswith(("!", "?")):
            weights.append(1.5)
        elif len(w) > 6:
            weights.append(1.2)
        else:
            weights.append(1.0)
    total_w = sum(weights)
    td = float(total_duration)
    timings: list[dict[str, Any]] = []
    t = 0.0
    for w, wt in zip(words, weights):
        d = td * (wt / total_w)
        timings.append({"word": w, "start": t, "end": t + d})
        t += d
    return timings


def estimate_word_timings(text: str, total_duration: float) -> list[dict[str, Any]]:
    """Alias for weighted_word_timings (caption sync API)."""
    return weighted_word_timings(text, total_duration)


def enhance_scenes(scenes: list) -> list[dict[str, Any]]:
    """
    Enrich splitter scenes with REL metadata (hook, pacing hints, caption prep, metrics).

    Sets rel_retention_layer=True on each scene so clip_planner can apply pacing.
    """
    out: list[dict[str, Any]] = []
    for i, raw in enumerate(scenes or []):
        if not isinstance(raw, dict):
            continue
        s = dict(raw)
        rng = _rng_for_scene(s, f"rel:{i}")
        st = str(s.get("type") or "point")
        text = str(s.get("text") or "")
        words = _word_count(text)
        # Speech: ~2.85 w/s heuristic for short-form VO
        est_speech = max(0.4, words / 2.85)
        wd = (words / est_speech) if est_speech > 0 else 0.0
        emphasis = _emphasis_points(text)
        eng = _infer_engagement_type(st, str(s.get("emotion") or ""), text)
        risk = _infer_retention_risk(st, text, wd)

        s["rel_retention_layer"] = True
        s["estimated_speech_duration"] = round(est_speech, 3)
        s["word_timings"] = weighted_word_timings(text, s["estimated_speech_duration"])
        s["word_density"] = round(wd, 4)
        s["emphasis_points"] = emphasis
        s["retention_risk"] = risk
        s["engagement_type"] = eng

        if st == "hook" and i == 0:
            hs = compute_hook_score(s)
            s["hook_strength"] = hs
            s["scroll_stop_probability"] = hs * 0.9
            s["pattern_interrupt"] = True
            s["intensity"] = "high"
        else:
            s["hook_strength"] = 0.0
            s["scroll_stop_probability"] = max(0.0, min(1.0, 0.25 + rng.random() * 0.35))
            s["pattern_interrupt"] = False

        out.append(s)
    return out


def _intensity_bucket(intensity: str) -> tuple[float, float]:
    it = (intensity or "medium").lower()
    if it == "high":
        return _PACING_HIGH
    if it == "low":
        return _PACING_LOW
    return _PACING_MEDIUM


def sample_clip_duration(
    scene: dict[str, Any],
    scene_index: int,
    clip_index: int,
    chunk: str,
) -> float:
    """
    Sample a clip duration with jitter (no static grid). Uses REL bounds when
    rel_retention_layer is set; otherwise returns 0 to signal caller to use legacy logic.
    """
    if not scene.get("rel_retention_layer"):
        return 0.0
    rng = _rng_for_scene(scene, f"dur:{scene_index}:{clip_index}:{chunk[:40]}")
    st = str(scene.get("type") or "point")
    first_hook = scene_index == 0 and st == "hook" and clip_index == 0
    if first_hook:
        return round(rng.uniform(HOOK_DURATION_MIN, HOOK_DURATION_MAX), 2)

    lo, hi = _intensity_bucket(str(scene.get("intensity") or "medium"))
    # Non-repeating feel: small irrational nudge
    span = hi - lo
    t = rng.random()
    jitter = 0.04 * span * math.sin(float(clip_index + 1) * 1.7 + rng.random())
    v = lo + t * span + jitter
    v = max(lo, min(hi, v))
    return round(v, 2)


def semantic_match_score(query: str, scene: dict[str, Any]) -> float:
    """
    Heuristic 0–1 alignment of query/chunk text to scene (no external APIs).
    Word overlap plus emotion/style match; generic-query penalty.
    """
    ql = (query or "").lower()
    score = 0.0
    q_words = set(ql.split())
    s_words = set(str(scene.get("text", "")).lower().split())
    overlap = len(q_words & s_words)
    score += overlap * 0.1

    emotion = scene.get("emotion")
    if emotion and str(emotion) in ql:
        score += 0.3

    style = scene.get("visual_style")
    if style and str(style) in ql:
        score += 0.2

    generic = {"business", "people", "city", "office", "team"}
    if any(g in ql for g in generic):
        score -= 0.2

    return max(0.0, min(score, 1.0))


def rerank_search_queries(chunk: str, scene: dict[str, Any], queries: list[str]) -> list[str]:
    """Prefer queries that overlap chunk keywords (stable sort)."""
    if not queries:
        return queries
    chunk_words = set(re.findall(r"[a-z]{3,}", (chunk or "").lower()))

    def score(q: str) -> float:
        qw = set(re.findall(r"[a-z]{3,}", q.lower()))
        return len(chunk_words & qw) + 0.01 * len(q)

    ranked = sorted(queries, key=score, reverse=True)
    # Preserve uniqueness
    seen: set[str] = set()
    out: list[str] = []
    for q in ranked:
        if q not in seen:
            seen.add(q)
            out.append(q)
    return out


def _pool_for_pair(prev: dict[str, Any], nxt: dict[str, Any]) -> list[str]:
    ptype = str(prev.get("type") or prev.get("scene_type") or "").lower()
    if ptype == "hook":
        return ["fast_cut"]
    pint = str(prev.get("intensity") or "medium").lower()
    nint = str(nxt.get("intensity") or "medium").lower()
    nsty = str(nxt.get("visual_style") or "").lower()
    if pint == "high" or nint == "high" or "fast" in nsty:
        return list(_HIGH_ENERGY_POOL)
    if pint == "low" and nint == "low":
        return ["fade", "blur_snap"]
    return ["fade", "zoomin", "blur_snap", "whip_pan"]


def _choose_weighted_transition(
    pool: list[str],
    history: list[str],
    rng: random.Random,
    preferred: str,
) -> str:
    if not pool:
        return "fade"
    if preferred in pool and preferred not in history[-3:]:
        return preferred
    return weighted_choice(pool, TRANSITION_WEIGHTS, history, rng)


def compute_between_transitions(flat: list[dict[str, Any]], *, seed: str = "rel") -> list[str]:
    """
    Build transition list (length n-1) with diversity: track last two names and
    avoid streaky repetition. Uses get_transition_filter as baseline, then REL pool.
    """
    if not flat or len(flat) < 2:
        return []
    rng = random.Random(int(hashlib.md5(seed.encode(), usedforsecurity=False).hexdigest()[:8], 16))
    out: list[str] = []
    history: list[str] = []

    for i in range(1, len(flat)):
        prev = dict(flat[i - 1])
        nxt = dict(flat[i])
        if not prev.get("type") and prev.get("scene_type"):
            prev["type"] = prev["scene_type"]
        if not nxt.get("type") and nxt.get("scene_type"):
            nxt["type"] = nxt["scene_type"]

        base = get_transition_filter(prev, nxt)
        pool = _pool_for_pair(prev, nxt)
        if base not in pool:
            pool = list(dict.fromkeys([base, *pool]))

        choice = _choose_weighted_transition(pool, history, rng, base)
        out.append(choice)
        history.append(choice)

    return out
