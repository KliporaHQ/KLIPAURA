"""
Influencer Engine — Concurrency test.

Simulates 20–50 concurrent jobs using threads; measures execution latency,
failure rate, queue delay.
"""

from __future__ import annotations

import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

SERVICE_ID = "influencer_engine"
DEFAULT_AVATARS = ["nova", "kai"]
DEFAULT_TOPICS = ["AI productivity", "ChatGPT tips", "Crypto news", "auto_discovered"]


def _load_avatars() -> List[str]:
    try:
        import yaml
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, "config", "avatar_profiles.yaml")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            avatars = data.get("avatars") or {}
            return list(avatars.keys()) if avatars else DEFAULT_AVATARS
    except Exception:
        pass
    return DEFAULT_AVATARS


def _schedule_one(service_manager: Any, payload: Dict[str, Any]) -> bool:
    if service_manager is not None and hasattr(service_manager, "schedule_job"):
        return service_manager.schedule_job(SERVICE_ID, payload) is not None
    try:
        from core.service_manager.core.task_dispatcher import dispatch_task
        return dispatch_task(service_id=SERVICE_ID, task_type="default", payload=payload) is not None
    except Exception:
        return False


def _run_one_job(
    job_id: int,
    service_manager: Any,
    avatars: List[str],
    topics: List[str],
    run_pipeline_inline: bool,
) -> Dict[str, Any]:
    """Run or schedule one job; return latency, success, queue_delay (0 if inline)."""
    payload = {
        "service_id": SERVICE_ID,
        "job_id": f"concurrency_test_{job_id}",
        "config": {
            "avatar_profile": random.choice(avatars),
            "topic": random.choice(topics),
            "blueprint": {"platform_target": "youtube_shorts"},
        },
    }
    t0 = time.perf_counter()
    if run_pipeline_inline:
        try:
            import sys
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            repo = os.path.dirname(os.path.dirname(base))
            for p in (base, repo):
                if p not in sys.path:
                    sys.path.insert(0, p)
            from pipeline import run as pipeline_run
            out = pipeline_run({"payload": payload})
            ok = out is not None and out.get("ok")
        except Exception:
            ok = False
        latency = time.perf_counter() - t0
        return {"latency": latency, "success": ok, "queue_delay": 0.0}
    # Schedule only
    queue_wait = time.perf_counter() - t0
    ok = _schedule_one(service_manager, payload)
    latency = time.perf_counter() - t0
    return {"latency": latency, "success": ok, "queue_delay": queue_wait}


def run_concurrency_test(
    service_manager: Any,
    num_jobs: int = 50,
    max_workers: Optional[int] = None,
    run_pipeline_inline: bool = True,
) -> Dict[str, Any]:
    """
    Run 20–50 (or num_jobs) concurrent jobs using a thread pool.
    Measures execution latency, failure rate, queue delay.
    """
    max_concurrent = _get_max_concurrent_jobs()
    if max_workers is None:
        max_workers = min(max_concurrent, num_jobs, 50)
    max_workers = min(max_workers, max_concurrent)
    num_jobs = min(num_jobs, _get_max_test_jobs())
    avatars = _load_avatars()
    topics = DEFAULT_TOPICS

    results: List[Dict[str, Any]] = []
    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _run_one_job, i, service_manager, avatars, topics, run_pipeline_inline
            ): i
            for i in range(num_jobs)
        }
        for fut in as_completed(futures):
            try:
                results.append(fut.result())
            except Exception:
                results.append({"latency": 0.0, "success": False, "queue_delay": 0.0})
    total_duration = time.perf_counter() - start

    completed = sum(1 for r in results if r.get("success"))
    failures = num_jobs - completed
    latencies = [r["latency"] for r in results if r.get("latency")]
    queue_delays = [r["queue_delay"] for r in results if r.get("queue_delay") is not None]

    return {
        "num_jobs": num_jobs,
        "max_workers": max_workers,
        "total_duration_seconds": round(total_duration, 3),
        "jobs_completed": completed,
        "failures": failures,
        "failure_rate": round(failures / num_jobs, 4) if num_jobs else 0,
        "execution_latency_avg_seconds": round(sum(latencies) / len(latencies), 3) if latencies else None,
        "execution_latency_max_seconds": round(max(latencies), 3) if latencies else None,
        "queue_delay_avg_seconds": round(sum(queue_delays) / len(queue_delays), 3) if queue_delays else None,
    }


def _get_max_test_jobs() -> int:
    try:
        import yaml
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, "config", "stress_test.yaml")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return int((yaml.safe_load(f) or {}).get("max_test_jobs", 1000))
    except Exception:
        pass
    return 1000


def _get_max_concurrent_jobs() -> int:
    try:
        import yaml
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, "config", "stress_test.yaml")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return int((yaml.safe_load(f) or {}).get("max_concurrent_jobs", 10))
    except Exception:
        pass
    return 10
