"""
UAE NMA-style AI disclosure + Windows-safe paths inside FFmpeg filtergraphs.

Paths embedded in ``-filter_complex`` (subtitles, drawtext fontfile) must escape
drive-letter colons for the FFmpeg option parser on Windows (``E:`` → ``E\\:``).
Prefer forward slashes; backslashes in paths are normalized away.
"""

from __future__ import annotations

import os

UAE_AI_DISCLOSURE_LABEL = "AI-Generated Content"


def ffmpeg_filtergraph_embed_path(path: str) -> str:
    """
    Sanitize an absolute filesystem path for use inside ``-filter_complex`` strings.

    - Normalize to forward slashes (FFmpeg accepts these on Windows).
    - Escape the drive-letter colon so ``E:/path`` becomes ``E\\:/path`` inside filters.
    """
    if not path:
        return path
    p = os.path.normpath(os.path.abspath(path)).replace("\\", "/")
    if len(p) > 1 and p[1] == ":":
        p = p[0] + "\\:" + p[2:]
    return p


def _escape_drawtext_literal(text: str) -> str:
    """Escape ``text='...'`` content for drawtext (colon, backslash, single quote)."""
    return (
        (text or "")
        .replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "'\\''")
    )


def uae_ai_disclosure_vf_chain() -> str:
    """
    Single-filter chain for ``-vf`` (no stream labels).

    Example: ``-vf "drawtext=..."``
    """
    t = _escape_drawtext_literal(UAE_AI_DISCLOSURE_LABEL)
    return (
        f"drawtext=text='{t}':fontcolor=white@0.5:fontsize=24:"
        f"x=w-tw-10:y=h-th-10"
    )


def uae_ai_disclosure_filter_complex(in_label: str, out_label: str) -> str:
    """
    One filtergraph segment: ``[in]drawtext=...[out]`` (stream labels without brackets).
    """
    t = _escape_drawtext_literal(UAE_AI_DISCLOSURE_LABEL)
    return (
        f"[{in_label}]drawtext=text='{t}':fontcolor=white@0.5:fontsize=24:"
        f"x=w-tw-10:y=h-th-10[{out_label}]"
    )


def zoompan_ken_burns_expr(
    duration_frames: int,
    out_w: int,
    out_h: int,
    fps: int,
    rate: str = "0.0015",
    cap: str = "1.1",
) -> str:
    """
    zoompan with explicit Ken Burns centering and ``d >= 1`` output frames.

    ``d`` is the number of *output* frames; must match the trimmed input length
    (see multicam ``_zoompan_output_frames``). Using ``d=1`` literally would collapse
    the clip to a single frame — we use ``d=max(1, duration_frames)`` instead.
    """
    d = max(1, int(duration_frames))
    return (
        f"zoompan=z='min(zoom+{rate},{cap})':d={d}:"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"s={out_w}x{out_h}:fps={fps}"
    )
