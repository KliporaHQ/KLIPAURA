"""Safe manifest updates from ``ugc_pipeline`` (JOB_ID in env). Keeps ``updated_at`` moving during long steps."""

from __future__ import annotations

import os
import traceback
from typing import Any


def pipeline_job_id() -> str | None:
    j = (os.environ.get("JOB_ID") or "").strip()
    return j or None


def touch_pipeline_manifest(stage: str, detail: str | None = None) -> None:
    """Record current pipeline stage + append to ``pipeline_stage_history`` (last 25 entries)."""
    jid = pipeline_job_id()
    if not jid:
        return
    try:
        from infrastructure.job_state import touch_manifest_stage

        touch_manifest_stage(jid, stage, detail)
    except Exception:
        pass


def fail_pipeline_manifest(exc: BaseException, *, extra_log: str = "") -> None:
    """Persist error + traceback before process exit (worker will also set DEAD_LETTER / log_tail from stdout)."""
    jid = pipeline_job_id()
    if not jid:
        return
    try:
        from infrastructure.job_state import touch_manifest_stage, update_manifest

        tb = traceback.format_exc()
        msg = f"{type(exc).__name__}: {exc}"
        combined = (extra_log + "\n" + tb + "\n" + msg).strip()
        tail = combined[-12000:] if len(combined) > 12000 else combined
        touch_manifest_stage(jid, "pipeline_error", msg[:400])
        update_manifest(jid, error=msg[:800], log_tail=tail)
    except Exception:
        pass
