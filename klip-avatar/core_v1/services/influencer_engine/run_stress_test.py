"""
Influencer Engine — Stress test entrypoint.

run_stress_test(service_manager, mode="inline"|"distributed"):
  1. warmup (10 jobs)
  2. medium load (100 jobs)
  3. burst load (500 jobs)
  4. collect metrics
  5. print summary

mode=inline: run pipeline directly in-process.
mode=distributed: start worker pool, submit jobs to queue, wait for completion, collect metrics.

Respects config/stress_test.yaml: max_test_jobs, max_concurrent_jobs.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from typing import Any, Dict, Optional

# Ensure repo root and service dir on path (so "testing" and "pipeline" resolve)
def _bootstrap():
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(here)
    for p in (repo_root, here):
        if p not in sys.path:
            sys.path.insert(0, p)


def _load_stress_config() -> Dict[str, Any]:
    try:
        import yaml
        base = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(base, "config", "stress_test.yaml")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
    except Exception:
        pass
    return {
        "max_test_jobs": 1000,
        "max_concurrent_jobs": 10,
        "warmup_jobs": 10,
        "medium_load_jobs": 100,
        "burst_load_jobs": 500,
        "concurrency_test_jobs": 50,
    }


def run_stress_test(
    service_manager: Any = None,
    run_inline: bool = True,
    skip_concurrency: bool = False,
    mode: str = "inline",
) -> Dict[str, Any]:
    """
    mode="inline": run pipeline in-process (same as run_inline=True).
    mode="distributed": start worker pool, submit jobs to queue, wait for completion, collect metrics.
    """
    _bootstrap()
    use_distributed = (mode or "inline").strip().lower() == "distributed"
    if use_distributed:
        return _run_stress_test_distributed(service_manager, skip_concurrency)

    cfg = _load_stress_config()
    warmup = min(cfg.get("warmup_jobs", 10), cfg.get("max_test_jobs", 1000))
    medium = min(cfg.get("medium_load_jobs", 100), cfg.get("max_test_jobs", 1000))
    burst = min(cfg.get("burst_load_jobs", 500), cfg.get("max_test_jobs", 1000))

    try:
        from testing.load_test import run_load_test
        from testing.concurrency_test import run_concurrency_test
    except ImportError:
        from services.influencer_engine.testing.load_test import run_load_test
        from services.influencer_engine.testing.concurrency_test import run_concurrency_test

    summary: Dict[str, Any] = {
        "phases": [],
        "warmup": None,
        "medium_load": None,
        "burst_load": None,
        "concurrency": None,
    }

    warmup_result = run_load_test(service_manager, num_jobs=warmup, run_inline=run_inline)
    summary["warmup"] = warmup_result
    summary["phases"].append(("warmup", warmup_result))

    medium_result = run_load_test(service_manager, num_jobs=medium, run_inline=run_inline)
    summary["medium_load"] = medium_result
    summary["phases"].append(("medium_load", medium_result))

    burst_result = run_load_test(service_manager, num_jobs=burst, run_inline=run_inline)
    summary["burst_load"] = burst_result
    summary["phases"].append(("burst_load", burst_result))

    if not skip_concurrency:
        concurrency_jobs = min(cfg.get("concurrency_test_jobs", 50), cfg.get("max_test_jobs", 1000))
        concurrency_result = run_concurrency_test(
            service_manager, num_jobs=concurrency_jobs, run_pipeline_inline=run_inline
        )
        summary["concurrency"] = concurrency_result
        summary["phases"].append(("concurrency", concurrency_result))

    _print_summary(summary)
    return summary


def _run_stress_test_distributed(
    service_manager: Any,
    skip_concurrency: bool,
) -> Dict[str, Any]:
    """Start worker pool, submit jobs to queue, wait for completion, collect metrics."""
    try:
        from klipaura_core.infrastructure.queue.queue_factory import get_queue
        from testing.load_test import run_load_test
        from testing.job_tracker import wait_for_completion
        from run_workers import start_worker_pool
    except ImportError:
        from klipaura_core.infrastructure.queue.queue_factory import get_queue
        from services.influencer_engine.testing.load_test import run_load_test
        from services.influencer_engine.testing.job_tracker import wait_for_completion
        from services.influencer_engine.run_workers import start_worker_pool

    cfg = _load_stress_config()
    num_workers = min(cfg.get("max_concurrent_jobs", 10), 10)
    warmup = min(cfg.get("warmup_jobs", 10), cfg.get("max_test_jobs", 1000))
    medium = min(cfg.get("medium_load_jobs", 100), cfg.get("max_test_jobs", 1000))
    burst = min(cfg.get("burst_load_jobs", 500), cfg.get("max_test_jobs", 1000))
    timeout = 300.0

    queue = get_queue("job")
    stop_flag = threading.Event()
    threads = start_worker_pool(service_manager, num_workers=num_workers, queue=queue, stop_flag=stop_flag)

    summary = {"mode": "distributed", "phases": [], "warmup": None, "medium_load": None, "burst_load": None}

    try:
        # 1. Warmup
        w = run_load_test(service_manager, num_jobs=warmup, queue=queue)
        summary["warmup"] = w
        job_ids = w.get("job_ids") or []
        if job_ids:
            r = wait_for_completion(job_ids, timeout=timeout)
            summary["warmup"]["jobs_completed"] = len(r["completed"])
            summary["warmup"]["failures"] = len(r["failed"])
            summary["warmup"]["pending"] = len(r["pending"])
        summary["phases"].append(("warmup", summary["warmup"]))

        # 2. Medium load
        m = run_load_test(service_manager, num_jobs=medium, queue=queue)
        summary["medium_load"] = m
        job_ids = m.get("job_ids") or []
        if job_ids:
            r = wait_for_completion(job_ids, timeout=timeout)
            summary["medium_load"]["jobs_completed"] = len(r["completed"])
            summary["medium_load"]["failures"] = len(r["failed"])
            summary["medium_load"]["pending"] = len(r["pending"])
        summary["phases"].append(("medium_load", summary["medium_load"]))

        # 3. Burst load
        b = run_load_test(service_manager, num_jobs=burst, queue=queue)
        summary["burst_load"] = b
        job_ids = b.get("job_ids") or []
        if job_ids:
            r = wait_for_completion(job_ids, timeout=timeout)
            summary["burst_load"]["jobs_completed"] = len(r["completed"])
            summary["burst_load"]["failures"] = len(r["failed"])
            summary["burst_load"]["pending"] = len(r["pending"])
        summary["phases"].append(("burst_load", summary["burst_load"]))

        # Aggregate metrics for example output
        total_submitted = (summary["warmup"].get("jobs_submitted") or 0) + (summary["medium_load"].get("jobs_submitted") or 0) + (summary["burst_load"].get("jobs_submitted") or 0)
        total_completed = (summary["warmup"].get("jobs_completed") or 0) + (summary["medium_load"].get("jobs_completed") or 0) + (summary["burst_load"].get("jobs_completed") or 0)
        total_failures = (summary["warmup"].get("failures") or 0) + (summary["medium_load"].get("failures") or 0) + (summary["burst_load"].get("failures") or 0)
        queue_depth_now = 0
        try:
            queue_depth_now = getattr(queue, "get_queue_depth", lambda: getattr(queue, "size", lambda: 0)())()
        except Exception:
            pass
        summary["aggregate"] = {
            "jobs_submitted": total_submitted,
            "jobs_completed": total_completed,
            "failures": total_failures,
            "processing_rate": round(total_completed / (timeout / 60.0), 2) if timeout and total_completed else 0,
            "queue_depth_peak": queue_depth_now,
        }
        summary["phases"].append(("aggregate", summary["aggregate"]))
    finally:
        stop_flag.set()
        for t in threads:
            t.join(timeout=2.0)

    _print_summary(summary)
    return summary


def _print_summary(summary: Dict[str, Any]) -> None:
    print("--- Stress test summary ---")
    for name, data in summary["phases"]:
        if not data:
            continue
        print(f"\n[{name}]")
        for k, v in data.items():
            if v is not None:
                print(f"  {k}: {v}")
    print("\n--- End summary ---")


if __name__ == "__main__":
    _bootstrap()
    run_stress_test(service_manager=None, run_inline=True, skip_concurrency=False)
