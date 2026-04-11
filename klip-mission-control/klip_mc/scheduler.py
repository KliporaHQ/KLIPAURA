"""
KLIPAURA OS — UAE-scheduled selector trigger (Mission Control co-process).

Runs selection cycles at 06:00 and 19:00 UAE (UTC+4). When AUTOPILOT_MODE is off,
logs the intended schedule only (no selector run — selector is a separate service).

# load_dotenv from old paths disabled — use repo root .env via klip_core / container env.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone, timedelta

from klip_core import get_logger, is_killed, publish_event

logger = get_logger("scheduler")
UAE_TZ = timezone(timedelta(hours=4))
SCHEDULE_HOURS_UAE = [6, 19]


def _autopilot_on() -> bool:
    return (os.getenv("AUTOPILOT_MODE") or "0").strip().lower() in ("1", "true", "yes", "on")


async def start_scheduler() -> None:
    """Background task: UAE clock, trigger events at 06:00 and 19:00 UAE."""
    logger.info(
        "scheduler_starting",
        timezone="UTC+4",
        schedule_hours=SCHEDULE_HOURS_UAE,
        autopilot=_autopilot_on(),
    )
    last_run_hour = -1

    while True:
        if await is_killed("scheduler"):
            await asyncio.sleep(30)
            continue

        now_uae = datetime.now(UAE_TZ)
        if now_uae.hour in SCHEDULE_HOURS_UAE and now_uae.hour != last_run_hour:
            last_run_hour = now_uae.hour
            if _autopilot_on():
                await publish_event(
                    module="scheduler",
                    event_type="cycle_triggered",
                    severity="info",
                    message=f"Scheduled cycle triggered at {now_uae.strftime('%H:%M')} UAE",
                )

                async def _flag_scanner_run() -> None:
                    try:
                        from klip_core.redis.client import get_redis_client
                        from klip_core.redis.queues import QUEUE_NAMES

                        def _set() -> None:
                            r = get_redis_client()
                            r.setex(QUEUE_NAMES.scanner_run_requested, 600, "1")

                        await asyncio.to_thread(_set)
                    except Exception:
                        logger.warning("scanner_run_requested redis set failed", exc_info=True)

                await _flag_scanner_run()
            else:
                await publish_event(
                    module="scheduler",
                    event_type="cycle_skipped",
                    severity="info",
                    message=(
                        f"AUTOPILOT_MODE off — would run selector at {now_uae.strftime('%H:%M')} UAE"
                    ),
                )

        await asyncio.sleep(60)
