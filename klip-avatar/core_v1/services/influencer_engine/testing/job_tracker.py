"""
Influencer Engine — Job completion tracking.

Tracks job_id, status (queued / running / completed / failed), start_time, end_time.
Store in Redis or in-memory fallback.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

REDIS_PREFIX = "ie:job:"
KEY_INDEX = "ie:jobs:index"
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"


def _redis():
    try:
        from klipaura_core.infrastructure.redis_client import get_redis_client
        return get_redis_client()
    except Exception:
        return None


_memory: Dict[str, Dict[str, Any]] = {}


def record_queued(job_id: str, payload: Optional[Dict[str, Any]] = None) -> None:
    """Mark job as queued."""
    record = {
        "job_id": job_id,
        "status": STATUS_QUEUED,
        "start_time": None,
        "end_time": None,
        "payload": payload,
    }
    r = _redis()
    if r:
        try:
            key = REDIS_PREFIX + job_id
            r.set(key, json.dumps(record))
            r.expire(key, 86400 * 2)
            r.sadd(KEY_INDEX, job_id)
            r.expire(KEY_INDEX, 86400 * 2)
        except Exception:
            pass
    _memory[job_id] = record


def record_running(job_id: str) -> None:
    """Mark job as running."""
    start = time.time()
    r = _redis()
    if r:
        try:
            key = REDIS_PREFIX + job_id
            raw = r.get(key)
            rec = json.loads(raw) if raw else {}
            rec["status"] = STATUS_RUNNING
            rec["start_time"] = start
            r.set(key, json.dumps(rec))
            r.expire(key, 86400 * 2)
        except Exception:
            pass
    if job_id in _memory:
        _memory[job_id]["status"] = STATUS_RUNNING
        _memory[job_id]["start_time"] = start
    else:
        _memory[job_id] = {"job_id": job_id, "status": STATUS_RUNNING, "start_time": start, "end_time": None}


def record_completed(job_id: str) -> None:
    """Mark job as completed."""
    end = time.time()
    r = _redis()
    if r:
        try:
            key = REDIS_PREFIX + job_id
            raw = r.get(key)
            rec = json.loads(raw) if raw else {}
            rec["status"] = STATUS_COMPLETED
            rec["end_time"] = end
            r.set(key, json.dumps(rec))
            r.expire(key, 86400 * 2)
        except Exception:
            pass
    if job_id in _memory:
        _memory[job_id]["status"] = STATUS_COMPLETED
        _memory[job_id]["end_time"] = end
    else:
        _memory[job_id] = {"job_id": job_id, "status": STATUS_COMPLETED, "start_time": None, "end_time": end}


def record_failed(job_id: str) -> None:
    """Mark job as failed."""
    end = time.time()
    r = _redis()
    if r:
        try:
            key = REDIS_PREFIX + job_id
            raw = r.get(key)
            rec = json.loads(raw) if raw else {}
            rec["status"] = STATUS_FAILED
            rec["end_time"] = end
            r.set(key, json.dumps(rec))
            r.expire(key, 86400 * 2)
        except Exception:
            pass
    if job_id in _memory:
        _memory[job_id]["status"] = STATUS_FAILED
        _memory[job_id]["end_time"] = end
    else:
        _memory[job_id] = {"job_id": job_id, "status": STATUS_FAILED, "start_time": None, "end_time": end}


def get_status(job_id: str) -> Optional[Dict[str, Any]]:
    """Get current status record for job."""
    r = _redis()
    if r:
        try:
            raw = r.get(REDIS_PREFIX + job_id)
            if raw:
                return json.loads(raw)
        except Exception:
            pass
    return _memory.get(job_id)


def wait_for_completion(
    job_ids: List[str],
    timeout: float = 300.0,
    poll_interval: float = 0.5,
) -> Dict[str, Any]:
    """
    Wait until all job_ids are completed or failed, or timeout.
    Returns { "completed": [...], "failed": [...], "pending": [...], "timed_out": bool }.
    """
    deadline = time.time() + timeout
    completed: List[str] = []
    failed: List[str] = []
    pending = list(job_ids)
    while pending and time.time() < deadline:
        still = []
        for jid in pending:
            rec = get_status(jid)
            if not rec:
                still.append(jid)
                continue
            s = rec.get("status")
            if s == STATUS_COMPLETED:
                completed.append(jid)
            elif s == STATUS_FAILED:
                failed.append(jid)
            else:
                still.append(jid)
        pending = still
        if pending:
            time.sleep(poll_interval)
    return {
        "completed": completed,
        "failed": failed,
        "pending": pending,
        "timed_out": len(pending) > 0,
    }
