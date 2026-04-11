#!/usr/bin/env python3
"""Print manifest summary, log_tail tail, pipeline_stage_history, and a simple health readout."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone


def _parse_iso_utc(s: str | None) -> datetime | None:
    if not s or not isinstance(s, str):
        return None
    t = s.strip()
    if not t:
        return None
    try:
        if t.endswith("Z"):
            t = t[:-1] + "+00:00"
        dt = datetime.fromisoformat(t)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _status_summary(m: dict) -> str:
    st = (m.get("status") or "").strip().upper()
    ps = (m.get("pipeline_stage") or "").lower()
    err = (m.get("error") or "").strip()
    ua = _parse_iso_utc(m.get("updated_at"))

    if st == "HITL_PENDING" or ps == "completed" or (m.get("final_video_path") and st not in ("FAILED", "DEAD_LETTER")):
        if m.get("funnel_url") or not m.get("payload", {}).get("generate_funnel"):
            return "Completed (terminal success path)"
        return "Completed video — funnel optional/pending"

    if st in ("DEAD_LETTER", "FAILED") or err:
        return f"Failed or terminal error ({st or 'unknown'})"

    if st == "PROCESSING" or st == "QUEUED" or st == "RETRYING":
        if ua is None:
            return "Running (cannot parse updated_at)"
        age = (datetime.now(timezone.utc) - ua).total_seconds()
        if age < 120:
            return "Running normally (recent manifest update)"
        if age < 600:
            return "Running — monitor if stage is not advancing"
        return "Likely stuck (no manifest update for several minutes)"

    return f"Unknown state (status={st or '?'})"


def main() -> int:
    p = argparse.ArgumentParser(description="Diagnose a single job manifest on disk")
    p.add_argument("job_id", help="Job UUID")
    args = p.parse_args()

    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if repo not in sys.path:
        sys.path.insert(0, repo)

    from infrastructure.job_state import read_manifest

    jid = args.job_id.strip()
    try:
        m = read_manifest(jid)
    except Exception as e:
        print("read_manifest failed:", e, file=sys.stderr)
        return 1

    ua = m.get("updated_at")
    ua_dt = _parse_iso_utc(ua if isinstance(ua, str) else None)
    age_sec = None
    if ua_dt is not None:
        age_sec = (datetime.now(timezone.utc) - ua_dt).total_seconds()

    print("=== manifest (summary) ===")
    print(
        json.dumps(
            {
                "job_id": m.get("job_id"),
                "status": m.get("status"),
                "updated_at": m.get("updated_at"),
                "pipeline_stage": m.get("pipeline_stage"),
                "pipeline_detail": m.get("pipeline_detail"),
                "error": m.get("error"),
                "r2_url": m.get("r2_url"),
                "funnel_url": m.get("funnel_url"),
                "final_video_path": m.get("final_video_path"),
            },
            indent=2,
        )
    )

    print("\n=== timing ===")
    if age_sec is not None:
        print(f"seconds_since_updated_at: {age_sec:.1f}")
    else:
        print("seconds_since_updated_at: (could not parse updated_at)")

    print("\n=== status summary ===")
    print(_status_summary(m))

    tail = (m.get("log_tail") or "").strip()
    lines = tail.splitlines()
    last10 = lines[-10:] if len(lines) > 10 else lines
    print("\n=== log_tail (last 10 lines) ===")
    if not last10:
        print("(empty)")
    else:
        print("\n".join(last10))

    hist = m.get("pipeline_stage_history")
    print("\n=== pipeline_stage_history (last 10 entries) ===")
    if isinstance(hist, list) and hist:
        for row in hist[-10:]:
            if isinstance(row, dict):
                print(f"  {row.get('at')}  {row.get('stage')}  {(row.get('detail') or '')[:120]}")
    else:
        print("(none)")

    print("\n=== full manifest JSON ===")
    print(json.dumps(m, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
