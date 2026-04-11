# Stress Test Prep — Deliverables

## Files created

| Path | Purpose |
|------|--------|
| `config/stress_test.yaml` | Safety limits: max_test_jobs (1000), max_concurrent_jobs (10), warmup/medium/burst/concurrency phase job counts |
| `testing/__init__.py` | Exports run_load_test, run_concurrency_test |
| `testing/load_test.py` | `run_load_test(service_manager, num_jobs=100, run_inline=False)` — submits jobs or runs pipeline inline; returns jobs_submitted, jobs_completed, failures, avg_execution_time |
| `testing/concurrency_test.py` | `run_concurrency_test(service_manager, num_jobs=50, run_pipeline_inline=True)` — thread-pool parallel execution; returns execution_latency, failure_rate, queue_delay |
| `run_stress_test.py` | `run_stress_test(service_manager)` — warmup (10) → medium (100) → burst (500) → concurrency (50); prints summary |

## System memory updated

**File:** `docs/system_memory/KLIPAURA_SYSTEM_MEMORY_CHECKPOINT.md`  

**Added:** Section *INFLUENCER ENGINE STATUS (PRODUCTION BUILD)* with capabilities, pipeline stages, scheduler, learning loop, limitations (in-memory queue/event bus, worker scaling not validated), and next step (Redis + worker pool).

## How to run

From repo root:

```bash
cd "KLIPORA MASTER AUTOMATION"
python -m services.influencer_engine.run_stress_test
```

Or from the service directory:

```bash
cd "KLIPORA MASTER AUTOMATION/services/influencer_engine"
python run_stress_test.py
```

With inline execution (default), the pipeline runs in-process so you get jobs_completed, failures, and avg_execution_time without a separate worker process.

## Suggested progression

Start small, then increase after infra upgrade:

- 10 → 50 → 100 → 300 (then Redis + workers → 500+)
