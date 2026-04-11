import os
from utils.logger import log
from pathlib import Path
from config import OUTPUT_DIR

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def save_video(video_path: str, content: bytes = None) -> str:
    """Save or ensure video file exists."""
    full_path = OUTPUT_DIR / "video.mp4"
    if content:
        full_path.write_bytes(content)
        log(f"Video saved to {full_path}", "STORAGE")
    else:
        # Create placeholder if not exists
        if not full_path.exists():
            # Create a simple MP4 placeholder (minimal valid mp4)
            with open(full_path, "wb") as f:
                f.write(b'\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42mp41')
            log(f"Created placeholder video at {full_path}", "STORAGE")
    return str(full_path)


def get_output_path() -> str:
    return str(OUTPUT_DIR / "video.mp4")


def ensure_output_dir():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    log(f"Output directory ready: {OUTPUT_DIR}", "STORAGE")
