# Phase-3: Autonomous Operation & Scheduling — Deliverables

## 1. Files created

| File | Purpose |
|------|--------|
| `config/avatar_profiles.yaml` | Multi-avatar configuration (nova, kai) with niche, tone, platforms, posting_frequency_per_day |
| `scheduler/influencer_scheduler.py` | `InfluencerScheduler` — load profiles, daily targets, generate job payloads, schedule via Service Manager, emit events |
| `scheduler/__init__.py` | Scheduler package exports |
| `run_scheduler.py` | `start_influencer_scheduler(service_manager)` — 10-minute loop: load profiles, pending posts, generate jobs, schedule |
| `agents/trend_agent.py` | `TrendAgent` with `discover_trends_for_niche(niche)` returning `[{ "topic", "score" }, ...]` |
| `agents/distribution_agent.py` | `DistributionAgent` with `optimize_platform_target(topic, avatar_profile)` (heuristic: ai_tools→youtube_shorts, crypto→x) |
| `agents/__init__.py` | Agent package exports |
| `service.yaml` | Service blueprint (influencer_engine, pipeline.run, agents, metrics, compliance) |
| `config.py` | Service config |
| `compliance.py` | Compliance hook |
| `metrics.py` | Metrics hook |
| `pipeline.py` | Pipeline entrypoint; uses `DistributionAgent.optimize_platform_target` when blueprint has no platform_target |

## 2. Files modified

- None. All changes are inside `services/influencer_engine/`. No core architecture modules were modified.

## 3. Example scheduler job payload

```json
{
  "service_id": "influencer_engine",
  "job_id": "auto_job_a1b2c3d4e5f67890",
  "config": {
    "avatar_profile": "nova",
    "topic": "Best AI tools for creators in 2025",
    "trend_score": 0.82,
    "blueprint": {
      "platform_target": "youtube_shorts"
    }
  }
}
```

This is the payload passed to `ServiceManager` (via `dispatch_task("influencer_engine", "default", payload)`). Workers receive it and pass `context.payload` to `pipeline.run(context)`.

## 4. Example emitted opportunity event

When `trend_score > 0.9`, the scheduler emits:

```json
{
  "type": "OPPORTUNITY_EVENT",
  "payload": {
    "service_id": "influencer_engine",
    "avatar": "nova",
    "topic": "Best AI tools for creators in 2025",
    "trend_score": 0.93
  }
}
```

Opportunity Scanner consumes this for strategy insights.

## 5. Example scheduler tick event

Each tick emits:

**SCHEDULER_TICK**
```json
{
  "type": "SCHEDULER_TICK",
  "payload": {
    "service_id": "influencer_engine",
    "timestamp": "2026-03-17T12:00:00.000000+00:00"
  }
}
```

**AVATAR_CONTENT_TARGET** (per avatar that had jobs generated)
```json
{
  "type": "AVATAR_CONTENT_TARGET",
  "payload": {
    "avatar": "nova",
    "jobs_generated": 4
  }
}
```

**JOBS_GENERATED**
```json
{
  "type": "JOBS_GENERATED",
  "payload": {
    "service_id": "influencer_engine",
    "jobs_generated": 7,
    "avatars": {
      "nova": 4,
      "kai": 3
    }
  }
}
```

---

## Running the scheduler

From repo root (with `PYTHONPATH` or `sys.path` including the repo):

```bash
python -m services.influencer_engine.run_scheduler
```

Or from code:

```python
from services.influencer_engine.run_scheduler import start_influencer_scheduler
start_influencer_scheduler(service_manager)  # service_manager optional; uses dispatch_task if None
```

The scheduler runs every 10 minutes, loads `config/avatar_profiles.yaml`, computes pending posts per avatar (capped by `posting_frequency_per_day` and Redis `ie:avatar:{id}:{date}` count), discovers trends via `TrendAgent.discover_trends_for_niche`, selects platform via `DistributionAgent.optimize_platform_target`, and schedules jobs via `core.service_manager.core.task_dispatcher.dispatch_task`.
