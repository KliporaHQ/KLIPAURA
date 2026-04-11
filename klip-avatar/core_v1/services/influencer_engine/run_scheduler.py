"""
Influencer Engine — Scheduler runtime entry.

Run the influencer scheduler loop every 10 minutes: load avatar profiles,
determine pending posts, generate job payloads, schedule jobs via Service Manager.
"""

from __future__ import annotations

import time
from typing import Any, Optional

# Architecture safeguard: ensure we do not modify core platform layers
try:
    from .platform_guard import ensure_platform_integrity, get_project_root
    ensure_platform_integrity(get_project_root())
except Exception:
    pass


def start_influencer_scheduler(service_manager: Any = None) -> None:
    """
    Run the scheduler loop every 10 minutes.

    - Load avatar profiles
    - Determine pending posts per avatar
    - Generate job payloads (trend-driven topic, distribution-optimized platform)
    - Schedule jobs via ServiceManager (dispatch_task when schedule_job not available)
    """
    from .scheduler.influencer_scheduler import InfluencerScheduler

    scheduler = InfluencerScheduler(service_manager=service_manager)
    interval_seconds = 10 * 60  # 10 minutes

    while True:
        try:
            result = scheduler.run_tick()
            jobs = result.get("jobs_generated", 0)
            avatars = result.get("avatars", {})
            if jobs or avatars:
                # Optional: log to stdout or telemetry
                pass
        except Exception:
            # Don't crash the loop
            pass
        time.sleep(interval_seconds)


if __name__ == "__main__":
    import os
    import sys
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    start_influencer_scheduler()
