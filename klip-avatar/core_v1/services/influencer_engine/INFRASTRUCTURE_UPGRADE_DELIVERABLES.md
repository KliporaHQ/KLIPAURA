# Infrastructure Upgrade + Stress Test — Deliverables

## 1. Files created

| Path | Purpose |
|------|--------|
| `services/influencer_engine/worker/__init__.py` | Worker package |
| `services/influencer_engine/worker/worker.py` | start_worker(service_manager, worker_id, queue, execute_fn, stop_flag) — dequeue → execute pipeline → ack/retry |
| `services/influencer_engine/run_workers.py` | start_worker_pool(service_manager, num_workers=5, queue, stop_flag) — threading |
| `services/influencer_engine/testing/job_tracker.py` | record_queued/running/completed/failed, get_status, wait_for_completion — Redis or in-memory |
| `Infrastructure/event_bus/redis_event_bus.py` | get_redis_pubsub_bus() — Redis Pub/Sub, fallback to in-memory EventBus |

## 2. Files modified

| Path | Changes |
|------|--------|
| `Infrastructure/queue/redis_queue.py` | dequeue_job(block=True, timeout=0) with BLPOP; ack_job(job_id); retry_job(job); get_queue_depth() |
| `Infrastructure/queue/local_queue.py` | dequeue_job(block=, timeout=); get_queue_depth(); ack_job(); retry_job() for interface parity |
| `Infrastructure/queue/queue_factory.py` | get_queue_backend() → "redis" or "mock" (REDIS_URL / Redis availability) |
| `Infrastructure/queue/__init__.py` | Export get_queue_backend |
| `services/influencer_engine/worker/worker.py` | Integrates job_tracker (record_running, record_completed, record_failed) |
| `services/influencer_engine/testing/load_test.py` | queue= parameter: enqueue to queue + record_queued; returns job_ids for distributed mode |
| `services/influencer_engine/run_stress_test.py` | mode="inline" \| "distributed"; _run_stress_test_distributed(): start worker pool, submit to queue, wait_for_completion, aggregate metrics |
| `services/influencer_engine/metrics.py` | get_service_metrics() extended: queue_depth, processing_rate, worker_utilization, end_to_end_latency |
| `docs/system_memory/KLIPAURA_SYSTEM_MEMORY_CHECKPOINT.md` | New section: INFRASTRUCTURE UPGRADE STATUS (queue, workers, stress testing, metrics, event bus, remaining) |

## 3. Example distributed stress test output

```
--- Stress test summary ---

[warmup]
  jobs_requested: 10
  jobs_submitted: 10
  jobs_completed: 10
  failures: 0
  pending: 0

[medium_load]
  jobs_requested: 100
  jobs_submitted: 100
  jobs_completed: 100
  failures: 0

[burst_load]
  jobs_requested: 500
  jobs_submitted: 500
  jobs_completed: 498
  failures: 2

[aggregate]
  jobs_submitted: 610
  jobs_completed: 608
  failures: 2
  processing_rate: 45.0
  queue_depth_peak: 0

--- End summary ---
```

Example get_service_metrics() after upgrade:

```json
{
  "service_id": "influencer_engine",
  "active_avatars": 2,
  "videos_generated_today": 0,
  "avg_engagement_rate": 0,
  "total_revenue": 0,
  "system_health": "healthy",
  "queue_depth": 0,
  "processing_rate": 0,
  "worker_utilization": 0,
  "end_to_end_latency": null
}
```

## 4. Backward compatibility

- **Pipeline:** Unchanged; still invoked as pipeline.run(context) with context["payload"].
- **Scheduler:** Unchanged; still uses dispatch_task / schedule_job (no queue dependency).
- **Inline mode:** run_stress_test(mode="inline") or run_inline=True unchanged.
- **Queue:** When REDIS_URL not set or Redis unavailable, get_queue() returns LocalQueue (queue_mock); existing callers that use dequeue_job() without block= still work (default block=False).

## 5. How to run distributed stress test

From repo root (with REDIS_URL or QUEUE_MODE=redis and Redis running):

```bash
cd "KLIPORA MASTER AUTOMATION"
python -m services.influencer_engine.run_stress_test
# Pass mode="distributed" from code, or:
# python -c "from services.influencer_engine.run_stress_test import run_stress_test; run_stress_test(mode='distributed')"
```

Without Redis, distributed mode still runs using LocalQueue (in-memory); workers and producer share the same process, so jobs are consumed and completion is tracked via job_tracker (in-memory fallback).
