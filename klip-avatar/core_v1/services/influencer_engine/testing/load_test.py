"""
Influencer Engine — Load test module.

Pushes num_jobs rapidly via ServiceManager; collects jobs_submitted, jobs_completed,
failures, avg_execution_time.
"""

from __future__ import annotations

import os
import random
import time
from typing import Any, Dict, List, Optional

SERVICE_ID = "influencer_engine"

# Default avatars/topics when config not loaded
DEFAULT_AVATARS = ["nova", "kai"]
DEFAULT_TOPICS = [
    "AI productivity",
    "ChatGPT tips",
    "Crypto news",
    "auto_discovered",
    "Trending tips",
    "How-to",
]


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
    """Schedule one job via ServiceManager or dispatch_task. Returns True if accepted."""
    if service_manager is not None and hasattr(service_manager, "schedule_job"):
        out = service_manager.schedule_job(SERVICE_ID, payload)
        return out is not None
    try:
        from core.service_manager.core.task_dispatcher import dispatch_task
        out = dispatch_task(service_id=SERVICE_ID, task_type="default", payload=payload)
        return out is not None
    except Exception:
        return False


def run_load_test(
    service_manager: Any,
    num_jobs: int = 100,
    avatars: Optional[List[str]] = None,
    topics: Optional[List[str]] = None,
    run_inline: bool = False,
    queue: Any = None,
) -> Dict[str, Any]:
    """
    Submit num_jobs with random avatar/topic.
    - run_inline=True: run pipeline in-process; returns jobs_completed, failures, avg_execution_time.
    - queue not None: enqueue to queue and record_queued (for distributed mode); returns job_ids.
    - else: schedule via ServiceManager.
    Returns metrics: jobs_submitted, jobs_completed, failures, avg_execution_time, job_ids (if queue).
    """
    max_jobs = _get_max_test_jobs()
    num_jobs = min(num_jobs, max_jobs)
    avatars = avatars or _load_avatars()
    topics = topics or DEFAULT_TOPICS

    jobs_completed = None
    failures = None
    avg_execution_time = None
    job_ids: Optional[List[str]] = None

    if queue is not None:
        job_ids = []
        start_submit = time.perf_counter()
        for i in range(num_jobs):
            job_id = f"load_test_{i}"
            payload = {
                "job_id": job_id,
                "service_id": SERVICE_ID,
                "payload": {
                    "service_id": SERVICE_ID,
                    "job_id": job_id,
                    "config": {
                        "avatar_profile": random.choice(avatars),
                        "topic": random.choice(topics),
                        "blueprint": {"platform_target": "youtube_shorts"},
                    },
                },
            }
            try:
                from .job_tracker import record_queued
                record_queued(job_id, payload)
            except Exception:
                pass
            if queue.enqueue_job(payload):
                job_ids.append(job_id)
        submit_duration = time.perf_counter() - start_submit
        jobs_submitted = len(job_ids)
        return {
            "jobs_requested": num_jobs,
            "jobs_submitted": jobs_submitted,
            "submit_duration_seconds": round(submit_duration, 3),
            "jobs_per_second": round(jobs_submitted / submit_duration, 2) if submit_duration > 0 else 0,
            "jobs_completed": None,
            "failures": None,
            "avg_execution_time": None,
            "job_ids": job_ids,
        }

    if run_inline:
        completed, failed, times = _run_jobs_inline(num_jobs, avatars, topics)
        jobs_submitted = completed + failed
        jobs_completed = completed
        failures = failed
        avg_execution_time = sum(times) / len(times) if times else None
        submit_duration = sum(times) if times else 0.0
    else:
        jobs_submitted = 0
        start_submit = time.perf_counter()
        for i in range(num_jobs):
            payload = {
                "service_id": SERVICE_ID,
                "job_id": f"load_test_{i}",
                "config": {
                    "avatar_profile": random.choice(avatars),
                    "topic": random.choice(topics),
                    "blueprint": {"platform_target": "youtube_shorts"},
                },
            }
            if _schedule_one(service_manager, payload):
                jobs_submitted += 1
        submit_duration = time.perf_counter() - start_submit

    return {
        "jobs_requested": num_jobs,
        "jobs_submitted": jobs_submitted,
        "submit_duration_seconds": round(submit_duration, 3),
        "jobs_per_second": round(jobs_submitted / submit_duration, 2) if submit_duration > 0 else 0,
        "jobs_completed": jobs_completed,
        "failures": failures,
        "avg_execution_time": round(avg_execution_time, 3) if avg_execution_time is not None else None,
    }


def _run_jobs_inline(
    num_jobs: int,
    avatars: List[str],
    topics: List[str],
) -> tuple:
    """Run pipeline.run() in-process for each job; return (completed, failures, execution_times)."""
    import sys
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    repo_root = os.path.dirname(os.path.dirname(base))
    for p in (base, repo_root):
        if p not in sys.path:
            sys.path.insert(0, p)
    pipeline_run = None
    try:
        from pipeline import run as pipeline_run
    except Exception:
        try:
            from services.influencer_engine.pipeline import run as pipeline_run
        except Exception:
            pass
    if not callable(pipeline_run):
        return (0, 0, [])
    completed = 0
    failures = 0
    times: List[float] = []
    for i in range(num_jobs):
        payload = {
            "service_id": SERVICE_ID,
            "job_id": f"load_test_{i}",
            "config": {
                "avatar_profile": random.choice(avatars),
                "topic": random.choice(topics),
                "blueprint": {"platform_target": "youtube_shorts"},
            },
        }
        context = {"payload": payload}
        start = time.perf_counter()
        try:
            out = pipeline_run(context)
            if out and out.get("ok"):
                completed += 1
            else:
                failures += 1
        except Exception:
            failures += 1
        times.append(time.perf_counter() - start)
    return (completed, failures, times)


def _get_max_test_jobs() -> int:
    try:
        import yaml
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, "config", "stress_test.yaml")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return int(data.get("max_test_jobs", 1000))
    except Exception:
        pass
    return 1000
