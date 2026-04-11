"""Caption / subtitle metadata for hook emphasis + scene highlights."""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any, Literal

# ASS primary BGR without &H prefix for inline tags (yellow accent)
ACCENT_BGR = "00D7FF"
# Emphasized keyword size (base Hook/Body sizes are in Style lines)
KEYWORD_FS = 96


def split_text_for_captions(text: str, max_words_per_line: int = 5) -> list[str]:
    """
    Split narration into short lines (about 3–5 words) for readable on-screen captions.
    """
    words = re.findall(r"\S+", (text or "").strip())
    if not words:
        return []
    lines: list[str] = []
    i = 0
    while i < len(words):
        n = max(3, min(max_words_per_line, 4 + (int(hashlib.md5(str(i).encode(), usedforsecurity=False).hexdigest(), 16) % 2)))
        lines.append(" ".join(words[i : i + n]))
        i += n
    return lines


def _short(s: str, max_len: int = 42) -> str:
    t = re.sub(r"\s+", " ", (s or "").strip())
    if len(t) <= max_len:
        return t
    return t[: max_len - 1].rstrip() + "…"


def _keyword_set_for_scene(scene: dict[str, Any]) -> set[str]:
    kws = scene.get("keywords") if isinstance(scene.get("keywords"), list) else []
    out: set[str] = set()
    for k in kws:
        if isinstance(k, str) and len(k) > 2:
            out.add(k.lower())
    for w in re.findall(r"[A-Za-z][A-Za-z0-9']+", str(scene.get("text") or "")):
        if len(w) > 5:
            out.add(w.lower())
    return out


def _ass_highlight_line(line: str, keywords: set[str]) -> str:
    """Highlight configured keywords/phrases (larger + accent); longest match wins."""
    if not keywords:
        return line
    klist = sorted({k.lower() for k in keywords if isinstance(k, str) and len(k.strip()) > 1}, key=len, reverse=True)
    if not klist:
        return line
    alts: list[str] = []
    for k in klist:
        if " " in k:
            alts.append(r"(?i)\b" + r"\s+".join(re.escape(w) for w in k.split()) + r"\b")
        else:
            alts.append(r"(?i)\b" + re.escape(k) + r"\b")
    try:
        pat = re.compile("|".join(alts))
    except re.error:
        return line
    big = f"{{\\c&H{ACCENT_BGR}&\\fs{KEYWORD_FS}\\b1}}"

    def _sub(m: re.Match) -> str:
        return f"{big}{m.group(0)}{{\\r}}"

    return pat.sub(_sub, line)


def generate_captions(scenes: list) -> list:
    """
    Build caption segments: strong hook (first ~2–3s equivalent), scene highlights, optional CTA line.

    Each item uses fractional timeline [0,1] scaled by final voice duration in the renderer:
    { "type": "hook"|"highlight"|"cta"|"subtitle", "text": "...", "start_frac": float, "end_frac": float }
    """
    if not scenes:
        return []
    out: list[dict[str, Any]] = []
    hook = next((s for s in scenes if isinstance(s, dict) and s.get("type") == "hook"), None)
    cta = next((s for s in scenes if isinstance(s, dict) and s.get("type") == "cta"), None)
    points = [s for s in scenes if isinstance(s, dict) and s.get("type") == "point"]

    if hook and str(hook.get("text") or "").strip():
        ht = _short(str(hook.get("text")), 120)
        lines = split_text_for_captions(ht, 3)
        hook_text = "\\N".join(_ass_highlight_line(L, _keyword_set_for_scene(hook)) for L in lines[:3])
        out.append(
            {
                "type": "hook",
                "text": hook_text,
                "start_frac": 0.0,
                "end_frac": 0.08,
            }
        )
    # Snappier pacing: shorter hook window, body starts earlier
    body_start = 0.09
    body_end = 0.88
    if points:
        span = body_end - body_start
        k = len(points)
        for i, p in enumerate(points):
            t0 = body_start + (span * i / k)
            t1 = body_start + (span * (i + 1) / k)
            raw = _short(str(p.get("text")), 120)
            lines = split_text_for_captions(raw, 4)
            kset = _keyword_set_for_scene(p if isinstance(p, dict) else {})
            body_text = "\\N".join(_ass_highlight_line(L, kset) for L in lines[:6])
            out.append(
                {
                    "type": "highlight",
                    "text": body_text,
                    "start_frac": t0,
                    "end_frac": t1,
                }
            )
    if cta and str(cta.get("text") or "").strip():
        ct = _short(str(cta.get("text")), 120)
        lines = split_text_for_captions(ct, 4)
        cta_text = "\\N".join(_ass_highlight_line(L, _keyword_set_for_scene(cta if isinstance(cta, dict) else {})) for L in lines[:3])
        out.append(
            {
                "type": "cta",
                "text": cta_text,
                "start_frac": 0.8,
                "end_frac": 1.0,
            }
        )
    return out


def write_ass_file(
    path: str,
    caption_rows: list[dict[str, Any]],
    total_duration_sec: float,
    width: int = 1080,
    height: int = 1920,
    caption_zone: Literal["full", "bottom"] = "full",
) -> None:
    """Write SSA/ASS for FFmpeg subtitles filter."""
    dur = max(1.0, float(total_duration_sec))
    # full: legacy hook center / CTA top — can overlap split product. bottom: affiliate split — text only in lower band.
    if caption_zone == "bottom":
        _bm_raw = (os.environ.get("CAPTION_BOTTOM_MARGIN") or "").strip()
        if _bm_raw:
            try:
                bm = max(40, min(400, int(float(_bm_raw))))
            except ValueError:
                bm = 92
            # Relative spacing vs legacy defaults (92 / 148 / 52)
            styles = f"""Style: Hook,Arial,88,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,108,108,0,0,1,4,3,2,50,50,{bm},1
Style: Body,Arial,50,&H00E8E8E8,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,2,55,55,{bm + 56},1
Style: Cta,Arial,58,&H00FFFFE0,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,2,2,45,45,{max(40, bm - 40)},1"""
        else:
            styles = """Style: Hook,Arial,88,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,108,108,0,0,1,4,3,2,50,50,92,1
Style: Body,Arial,50,&H00E8E8E8,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,2,55,55,148,1
Style: Cta,Arial,58,&H00FFFFE0,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,2,2,45,45,52,1"""
    else:
        styles = """Style: Hook,Arial,88,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,108,108,0,0,1,4,3,5,50,50,0,1
Style: Body,Arial,50,&H00E8E8E8,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,2,60,60,150,1
Style: Cta,Arial,58,&H00FFFFE0,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,2,8,40,40,110,1"""
    header = f"""[Script Info]
Title: KLIP Cinematic V2
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{styles}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for row in caption_rows or []:
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        t0 = float(row.get("start_frac", 0.0)) * dur
        t1 = float(row.get("end_frac", 0.1)) * dur
        t0 = max(0.0, min(dur, t0))
        t1 = max(t0 + 0.2, min(dur, t1))
        st = _ass_time(t0)
        en = _ass_time(t1)
        rtype = row.get("type")
        if rtype == "cta":
            raw_ct = (os.environ.get("CAPTION_CTA_TIMING") or os.environ.get("AFFILIATE_CTA_TIMING") or "").strip().lower()
            if raw_ct:
                if raw_ct in ("final_20pct", "final_20%", "final_20"):
                    t0 = max(0.0, dur * 0.80)
                    t1 = dur
                elif raw_ct == "final_3s":
                    t0 = max(0.0, dur - 3.0)
                    t1 = dur
                elif raw_ct == "final_5s":
                    t0 = max(0.0, dur - 5.0)
                    t1 = dur
                elif raw_ct == "final_10s":
                    t0 = max(0.0, dur - 10.0)
                    t1 = dur
                st = _ass_time(t0)
                en = _ass_time(max(t0 + 0.2, t1))
        if rtype == "hook":
            style = "Hook"
        elif rtype == "cta":
            style = "Cta"
        else:
            style = "Body"
        if "\\N" in text or "{\\" in text:
            esc = text
        else:
            esc = text.replace("{", "\\{").replace("}", "\\}")
        lines.append(f"Dialogue: 0,{st},{en},{style},,0,0,0,,{esc}\n")

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def _ass_time(sec: float) -> str:
    sec = max(0.0, sec)
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    cs = int(round((s - int(s)) * 100))
    s_int = int(s)
    return f"{h:d}:{m:02d}:{s_int:02d}.{cs:02d}"
