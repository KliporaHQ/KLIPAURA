"""
Unit pricing for cost calculation (WaveSpeed + LLM).

Rates aligned with WaveSpeedAI and LLM provider pricing so job costs
are computed from usage and displayed on the Cost & Revenue page.
"""

from __future__ import annotations

import os
from typing import Any, Dict

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# --- WaveSpeed (video) — Wan 2.2 I2V 480p Ultra Fast $0.01/sec
VIDEO_USD_PER_SEC = 0.01

# --- WaveSpeed (image) — Flux Dev Ultra Fast / Z-Image: $0.005 per 200 images
IMAGE_USD_PER_IMAGE = 0.005 / 200  # 0.000025

# --- LLM (Qwen3 Max 128K) — $0.0012/1K input, $0.006/1K output (overridable via env)
LLM_INPUT_USD_PER_1K = float(os.environ.get("LLM_INPUT_USD_PER_1K", "0.0012"))
LLM_OUTPUT_USD_PER_1K = float(os.environ.get("LLM_OUTPUT_USD_PER_1K", "0.006"))

# --- TTS (placeholder; adjust when provider pricing is fixed)
TTS_USD_PER_CHAR = float(os.environ.get("TTS_USD_PER_CHAR", "0.00002"))  # ~$0.01 per 500 chars


def compute_llm_cost(input_tokens: int = 0, output_tokens: int = 0) -> float:
    """Compute LLM cost from token counts."""
    return (input_tokens / 1000.0 * LLM_INPUT_USD_PER_1K) + (output_tokens / 1000.0 * LLM_OUTPUT_USD_PER_1K)


def compute_video_cost(duration_sec: float) -> float:
    """Compute video (I2V) cost from generated seconds (Wan 2.2 Ultra Fast)."""
    return round(duration_sec * VIDEO_USD_PER_SEC, 4)


def compute_image_cost(num_images: int = 1) -> float:
    """Compute image generation cost (Flux Dev / Z-Image)."""
    return round(num_images * IMAGE_USD_PER_IMAGE, 6)


def compute_tts_cost(characters: int = 0) -> float:
    """Compute TTS cost from character count (placeholder rate)."""
    return round(characters * TTS_USD_PER_CHAR, 4)


def get_pricing_config() -> Dict[str, Any]:
    """Return unit pricing for display on Cost & Revenue page."""
    return {
        "llm": {
            "input_per_1k_tokens_usd": LLM_INPUT_USD_PER_1K,
            "output_per_1k_tokens_usd": LLM_OUTPUT_USD_PER_1K,
            "note": "Qwen3 Max 128K (override via LLM_INPUT_USD_PER_1K / LLM_OUTPUT_USD_PER_1K)",
        },
        "tts": {
            "per_char_usd": TTS_USD_PER_CHAR,
            "note": "Placeholder; set TTS_USD_PER_CHAR for your provider.",
        },
        "image": {
            "per_image_usd": IMAGE_USD_PER_IMAGE,
            "note": "WaveSpeed Flux Dev / Z-Image ($0.005 per 200 images).",
        },
        "video": {
            "per_second_usd": VIDEO_USD_PER_SEC,
            "note": "WaveSpeed Wan 2.2 I2V 480p Ultra Fast.",
        },
        "links": {
            "wavespeed": "https://wavespeed.ai",
            "wavespeed_docs": "https://wavespeed.ai/docs",
        },
    }
