"""
FFmpeg burn-in: social handle + SEO caption line for exports (identity / discovery).

Respects ``KLIP_BURN_BRAND_WATERMARK=0`` to disable.
Only @AriaVedaAI and @VanceTechReview are authorized watermark identities.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from typing import Any, Dict, Optional

from .ffmpeg_uae_compliance import ffmpeg_filtergraph_embed_path, uae_ai_disclosure_vf_chain

# Authorized watermark identities — matches disk_profiles.PRIMARY_SYSTEM_IDENTITIES.
_AUTHORIZED_HANDLES: Dict[str, str] = {
    "aria_veda": "@AriaVedaAI",
    "kael_vance": "@VanceTechReview",
}
_AUTHORIZED_HANDLE_SET: frozenset[str] = frozenset(_AUTHORIZED_HANDLES.values())


def _resolve_authorized_handle(config: Dict[str, Any]) -> str:
    """
    Return the canonical watermark handle for the active persona.

    Lookup order: explicit ``social_handle`` in config (if authorized) →
    ``avatar_id`` mapping → Aria Veda default (@AriaVedaAI).
    """
    ap = config.get("avatar_profile") if isinstance(config.get("avatar_profile"), dict) else {}
    explicit = (
        config.get("social_handle") or ap.get("social_handle") or ap.get("handle") or ""
    ).strip()
    if explicit in _AUTHORIZED_HANDLE_SET:
        return explicit
    avatar_id = (config.get("avatar_id") or ap.get("avatar_id") or "").strip().lower()
    if avatar_id in _AUTHORIZED_HANDLES:
        return _AUTHORIZED_HANDLES[avatar_id]
    return _AUTHORIZED_HANDLES["aria_veda"]


def _ffmpeg_exe() -> str:
    try:
        from .ffmpeg_path import get_ffmpeg_exe

        return get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def _default_fontfile() -> Optional[str]:
    if os.name == "nt":
        w = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", "arial.ttf")
        return w if os.path.isfile(w) else None
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/TTF/DejaVuSans.ttf"):
        if os.path.isfile(p):
            return p
    return None


def _esc_drawtext(s: str) -> str:
    """Minimal escaping for ffmpeg drawtext text= on Windows paths and quotes."""
    return (
        (s or "")
        .replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "'\\''")
    )


def burn_social_and_seo_watermark(input_mp4: str, config: Dict[str, Any]) -> Optional[str]:
    """
    Re-encode video with two-line overlay: handle + compact SEO (title + hashtags).
    Overwrites ``input_mp4`` in place when possible. Returns path on success.
    """
    if (os.environ.get("KLIP_BURN_BRAND_WATERMARK") or "1").strip().lower() in ("0", "false", "no"):
        return None
    if not input_mp4 or not os.path.isfile(input_mp4):
        return None
    ap = config.get("avatar_profile") if isinstance(config.get("avatar_profile"), dict) else {}
    handle = _resolve_authorized_handle(config)
    title = (config.get("seo_title") or ap.get("seo_title") or "").strip()
    tags = (config.get("seo_hashtags") or ap.get("seo_hashtags") or "").strip()
    line1 = handle
    line2 = " · ".join(x for x in (title[:120], tags[:160]) if x).strip()
    if not line2:
        line2 = "KLIPAURA"

    fd2, out_path = tempfile.mkstemp(suffix="_wm.mp4", prefix="wm_")
    os.close(fd2)
    exe = _ffmpeg_exe()
    font = _default_fontfile()
    ff_esc = ffmpeg_filtergraph_embed_path(font) if font else ""
    t1 = _esc_drawtext(line1)
    t2 = _esc_drawtext(line2)
    uae = uae_ai_disclosure_vf_chain()
    if font:
        vf = (
            f"{uae},"
            f"drawtext=fontfile='{ff_esc}':text='{t1}':fontcolor=white:fontsize=24:"
            f"box=1:boxcolor=black@0.55:boxborderw=4:x=(w-text_w)/2:y=h-112,"
            f"drawtext=fontfile='{ff_esc}':text='{t2}':fontcolor=white:fontsize=17:"
            f"box=1:boxcolor=black@0.45:boxborderw=4:x=(w-text_w)/2:y=h-56"
        )
    else:
        vf = (
            f"{uae},"
            f"drawtext=text='{t1}':fontcolor=white:fontsize=24:"
            f"box=1:boxcolor=black@0.55:boxborderw=4:x=(w-text_w)/2:y=h-112,"
            f"drawtext=text='{t2}':fontcolor=white:fontsize=17:"
            f"box=1:boxcolor=black@0.45:boxborderw=4:x=(w-text_w)/2:y=h-56"
        )
    cmd = [exe, "-y", "-i", input_mp4, "-vf", vf, "-c:a", "copy", "-c:v", "libx264", "-pix_fmt", "yuv420p", out_path]
    try:
        subprocess.run(cmd, capture_output=True, timeout=300, check=True)
        if os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
            try:
                os.replace(out_path, input_mp4)
            except Exception:
                try:
                    os.unlink(input_mp4)
                    shutil.move(out_path, input_mp4)
                except Exception:
                    return out_path
            return input_mp4
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        pass
    try:
        if os.path.isfile(out_path):
            os.unlink(out_path)
    except Exception:
        pass
    return None
