"""
In-memory UGC job store (single-slot execution; no Redis yet).
"""

from __future__ import annotations

import threading
import time
from typing import Any

JOB_STORE: dict[str, dict[str, Any]] = {}

_slot_lock = threading.Lock()
_generation_in_progress = False


def acquire_generation_slot() -> bool:
    """Return True if this request may start a pipeline run (one at a time)."""
    global _generation_in_progress
    with _slot_lock:
        if _generation_in_progress:
            return False
        _generation_in_progress = True
        return True


def release_generation_slot() -> None:
    global _generation_in_progress
    with _slot_lock:
        _generation_in_progress = False


def create_job() -> str:
    job_id = str(int(time.time() * 1000))
    JOB_STORE[job_id] = {
        "status": "started",
        "log": "",
        "output": None,
    }
    return job_id


def update_job(job_id: str, **kwargs: Any) -> None:
    if job_id in JOB_STORE:
        JOB_STORE[job_id].update(kwargs)


def get_job(job_id: str) -> dict[str, Any] | None:
    return JOB_STORE.get(job_id)
