#!/usr/bin/env python3
"""
Twice-daily UAE selector cycles + optional guardian heartbeat.

Environment:
  AUTOPILOT_MODE=1  — run daemon (selector + guardian on interval)
  AUTOPILOT_MODE=0  — print schedule and exit (no jobs enqueued)

  SELECTOR_AT_MORNING=06:00  (UAE local time, default 06:00)
  SELECTOR_AT_EVENING=19:00 (default 19:00)
  GUARDIAN_INTERVAL_MIN=30
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import schedule

_REPO = Path(__file__).resolve().parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
_KLIP_SCANNER_ROOT = _REPO / "klip-scanner"
if _KLIP_SCANNER_ROOT.is_dir() and str(_KLIP_SCANNER_ROOT) not in sys.path:
    sys.path.insert(0, str(_KLIP_SCANNER_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO / ".env", override=False)
except ImportError:
    pass


def _utc_times_for_uae_hours() -> tuple[str, str]:
    """Return (morning, evening) as 'HH:MM' in UTC — shared with `infrastructure.autopilot_info`."""
    from infrastructure.autopilot_info import uae_hours_to_utc_strings

    a, b, _ = uae_hours_to_utc_strings()
    return a, b


def _run_selector() -> None:
    from infrastructure.scheduler_budget import video_budget_allows
    from klip_selector.selector_worker import run_cycle

    if not video_budget_allows():
        print("[scheduler] budget gate closed — skip selector", flush=True)
        return
    run_cycle()


def _guardian() -> None:
    from infrastructure.system_guardian import log_guardian_event

    log_guardian_event()


def main() -> None:
    mode = (os.getenv("AUTOPILOT_MODE") or "0").strip().lower()
    morning_utc, evening_utc = _utc_times_for_uae_hours()

    if mode not in ("1", "true", "yes", "on"):
        print("AUTOPILOT_MODE is off — schedule only (no execution):", flush=True)
        print(f"  Would run selector at {morning_utc} UTC and {evening_utc} UTC (06:00 / 19:00 UAE)", flush=True)
        print("  Guardian every 30 min. Set AUTOPILOT_MODE=1 to run.", flush=True)
        return

    schedule.every().day.at(morning_utc).do(_run_selector)
    schedule.every().day.at(evening_utc).do(_run_selector)
    interval = int(os.getenv("GUARDIAN_INTERVAL_MIN", "30"))
    schedule.every(interval).minutes.do(_guardian)

    print(f"[scheduler] AUTOPILOT_MODE=1 selector@{morning_utc},{evening_utc} UTC guardian={interval}m", flush=True)
    _guardian()
    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    main()
