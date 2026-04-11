"""UAE → UTC schedule preview for Mission Control (same math as `scheduler.py`)."""

from __future__ import annotations

import os
from datetime import datetime

from zoneinfo import ZoneInfo

UAE = ZoneInfo("Asia/Dubai")


def uae_hours_to_utc_strings() -> tuple[str, str, dict[str, int]]:
    """Return (morning_utc, evening_utc, {"uae_morning": h, "uae_evening": h}) as HH:MM."""
    uae_hour_morning = int(os.getenv("SELECTOR_UAE_MORNING_HOUR", "6"))
    uae_hour_evening = int(os.getenv("SELECTOR_UAE_EVENING_HOUR", "19"))

    def uae_to_utc(h: int, m: int = 0) -> tuple[int, int]:
        now = datetime(2000, 1, 1, h, m, tzinfo=UAE)
        utc = now.astimezone(ZoneInfo("UTC"))
        return utc.hour, utc.minute

    mh, mm = uae_to_utc(uae_hour_morning, 0)
    eh, em = uae_to_utc(uae_hour_evening, 0)
    return (
        f"{mh:02d}:{mm:02d}",
        f"{eh:02d}:{em:02d}",
        {"uae_morning": uae_hour_morning, "uae_evening": uae_hour_evening},
    )
