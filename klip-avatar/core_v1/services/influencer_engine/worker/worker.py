"""
Influencer Engine — Worker.

Polls queue, runs pipeline (or service_manager.execute_service), ack or retry.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, Callable, Optional

SERVICE_ID = "influencer_engine"
DEQUEUE_BLOCK_TIMEOUT = 5


def start_worker(
    service_manager: Any,
    worker_id: str,
    queue: Any = None,
    execute_fn: Optional[Callable[[dict], Any]] = None,
    stop_flag: Any = None,
) -> None:
    """
    Run worker loop: dequeue_job(block=True) -> execute -> ack_job or retry_job.
    If queue is None, obtain via Infrastructure.queue.queue_factory.get_queue("job").
    execute_fn: callable(context) to run one job; default is pipeline.run from this service.
    stop_flag: optional object with .is_set() to stop the loop (e.g. threading.Event).
    """
    if queue is None:
        _ensure_path()
        try:
            from klipaura_core.infrastructure.queue.queue_factory import get_queue
        except ImportError:
            raise RuntimeError("klipaura_core not on PYTHONPATH — cannot create queue")
        queue = get_queue("job")

    if execute_fn is None:
        execute_fn = _get_pipeline_run()

    while True:
        if stop_flag is not None and getattr(stop_flag, "is_set", lambda: False)():
            break
        job = None
        try:
            job = queue.dequeue_job(block=True, timeout=DEQUEUE_BLOCK_TIMEOUT)
        except TypeError:
            job = queue.dequeue_job()
        if job is None:
            continue
        job_id = (job.get("job_id") or (job.get("payload") or {}).get("job_id") or "unknown")
        _track_running(job_id)
        try:
            context = {"payload": job.get("payload") or job}
            if service_manager is not None and hasattr(service_manager, "execute_service"):
                result = service_manager.execute_service(SERVICE_ID, context)
            else:
                result = execute_fn(context)
            queue.ack_job(job_id)
            _track_completed(job_id)
        except Exception:
            _track_failed(job_id)
            try:
                queue.retry_job(job)
            except Exception:
                pass


def _track_running(job_id: str) -> None:
    try:
        from services.influencer_engine.testing.job_tracker import record_running
        record_running(job_id)
    except Exception:
        try:
            from testing.job_tracker import record_running
            record_running(job_id)
        except Exception:
            pass


def _track_completed(job_id: str) -> None:
    try:
        from services.influencer_engine.testing.job_tracker import record_completed
        record_completed(job_id)
    except Exception:
        try:
            from testing.job_tracker import record_completed
            record_completed(job_id)
        except Exception:
            pass


def _track_failed(job_id: str) -> None:
    try:
        from services.influencer_engine.testing.job_tracker import record_failed
        record_failed(job_id)
    except Exception:
        try:
            from testing.job_tracker import record_failed
            record_failed(job_id)
        except Exception:
            pass


def _ensure_path() -> None:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    repo_root = os.path.dirname(here)
    for p in (repo_root, here):
        if p not in sys.path:
            sys.path.insert(0, p)


def _get_pipeline_run() -> Callable[[dict], Any]:
    _ensure_path()
    try:
        from services.influencer_engine.pipeline import run
        return run
    except ImportError:
        from pipeline import run
        return run
