"""Central video dimensions and duration tiers for cinematic_v2 + Core V1."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

AspectRatio = Literal["9:16", "1:1", "16:9"]
DurationTier = Literal["SHORT", "STANDARD", "LONG"]

ASPECT_MAP: dict[str, tuple[int, int]] = {
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "16:9": (1920, 1080),
}

DURATION_MAP: dict[str, int] = {
    "SHORT": 6,
    "STANDARD": 12,
    "LONG": 20,
}

MAX_DURATION_SEC = 30.0


@dataclass(frozen=True)
class ResolvedVideoConfig:
    aspect_ratio: str
    duration_tier: str
    width: int
    height: int
    duration_seconds: float


def validate_aspect_ratio(value: str) -> str:
    k = (value or "").strip()
    if k not in ASPECT_MAP:
        raise ValueError(f"Invalid aspect_ratio: {value!r}; expected one of {tuple(ASPECT_MAP)}")
    return k


def validate_duration_tier(value: str) -> str:
    k = (value or "").strip().upper()
    if k not in DURATION_MAP:
        raise ValueError(f"Invalid duration_tier: {value!r}; expected one of {tuple(DURATION_MAP)}")
    return k


def resolve_dimensions(aspect_ratio: str) -> tuple[int, int]:
    ar = validate_aspect_ratio(aspect_ratio)
    return ASPECT_MAP[ar]


def resolve_duration_seconds(duration_tier: str) -> float:
    dt = validate_duration_tier(duration_tier)
    sec = float(DURATION_MAP[dt])
    if sec > MAX_DURATION_SEC:
        raise ValueError(f"duration tier exceeds cap ({MAX_DURATION_SEC}s)")
    return sec


def resolve_video_config(aspect_ratio: str = "9:16", duration_tier: str = "SHORT") -> ResolvedVideoConfig:
    ar = validate_aspect_ratio(aspect_ratio)
    dt = validate_duration_tier(duration_tier)
    w, h = ASPECT_MAP[ar]
    d = float(DURATION_MAP[dt])
    return ResolvedVideoConfig(
        aspect_ratio=ar,
        duration_tier=dt,
        width=w,
        height=h,
        duration_seconds=d,
    )
