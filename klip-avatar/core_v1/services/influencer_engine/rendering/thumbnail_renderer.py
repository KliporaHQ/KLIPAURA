"""
Influencer Engine — Thumbnail renderer.

Generates thumbnail image with text overlay. Uses Pillow (local) or image API from .env.
"""

from __future__ import annotations

import os
import tempfile
from typing import Any, Dict, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


def _render_thumbnail_pillow(
    title: str,
    output_path: str,
    width: int = 1280,
    height: int = 720,
    bg_color: str = "#1a1a2e",
    text_color: str = "#eee",
) -> bool:
    """Generate thumbnail with Pillow: solid background + text overlay. Returns True if ok."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return False
    try:
        img = Image.new("RGB", (width, height), bg_color)
        draw = ImageDraw.Draw(img)
        # Simple text: wrap title to fit
        font = ImageFont.load_default()
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 48)
        except Exception:
            try:
                font = ImageFont.truetype("arial.ttf", 48)
            except Exception:
                pass
        # Center text (rough wrap at ~40 chars)
        lines = []
        words = (title or "Video")[:80].split()
        line = ""
        for w in words:
            if len(line) + len(w) + 1 <= 40:
                line = f"{line} {w}".strip() if line else w
            else:
                if line:
                    lines.append(line)
                line = w
        if line:
            lines.append(line)
        y = (height - len(lines) * 50) // 2
        for ln in lines:
            try:
                bbox = draw.textbbox((0, 0), ln, font=font)
                tw = bbox[2] - bbox[0]
            except AttributeError:
                tw, _ = draw.textsize(ln, font=font)
            x = (width - tw) // 2
            draw.text((x, y), ln, fill=text_color, font=font)
            y += 50
        img.save(output_path, "PNG")
        return os.path.isfile(output_path)
    except Exception:
        return False


class ThumbnailRenderer:
    """Renders thumbnail from config: title/topic, optional avatar_id."""

    def render(
        self,
        config: Dict[str, Any],
        output_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Produce thumbnail asset. Uses Pillow (local) to create image with text overlay.
        config: title, topic, avatar_id. Returns dict with url, path.
        """
        title = config.get("title") or config.get("topic") or "video"
        avatar_id = config.get("avatar_id") or "default"
        if not output_path:
            fd, output_path = tempfile.mkstemp(suffix=".png", prefix="thumb_")
            os.close(fd)
        ok = _render_thumbnail_pillow(title, output_path)
        if ok:
            return {
                "url": f"file://{output_path}",
                "title": title,
                "avatar_id": avatar_id,
                "path": output_path,
                "mock": False,
            }
        url = "file://thumb/thumb.png" if output_path else "mock://thumb/thumb.png"
        return {
            "url": url,
            "title": title,
            "avatar_id": avatar_id,
            "path": output_path,
            "mock": True,
        }
