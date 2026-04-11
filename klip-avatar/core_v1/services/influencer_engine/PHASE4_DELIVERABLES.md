# Phase-4: AI Influencer Growth Loop — Deliverables

## 1. Files created

| File | Purpose |
|------|--------|
| `agents/analytics_agent.py` | **AnalyticsAgent** — `collect_performance(video_asset, distribution_result, avatar_profile)` → `performance_metrics`, `performance_score` (views, likes, shares, watch_time, engagement_rate, follower_growth) |
| `learning/performance_model.py` | **calculate_performance_score(metrics)** — weighted formula (views, engagement_rate, watch_time, follower_growth), returns 0.0–1.0 |
| `learning/strategy_memory.py` | Strategy memory: **get_strategy(avatar_id)**, **save_strategy(avatar_id, strategy)**, **update_from_performance(...)**, **record_experiment_result(experiment_id, variant, score)**; Redis-backed with in-memory fallback |
| `learning/__init__.py` | Learning package exports |

## 2. Files modified

| File | Changes |
|------|--------|
| `scheduler/influencer_scheduler.py` | A/B variants: **generate_job_payload** accepts `experiment_id`, `variant`, `hook`; **_get_ab_hooks**, **_bias_topic_from_strategy**, **_platform_from_strategy**; **run_tick** loads strategy, biases topic/platform, creates two jobs per topic when `pending >= 2` with same `experiment_id` and variants A/B |
| `pipeline.py` | **analyze_performance(context)** stage: AnalyticsAgent → CONTENT_PERFORMANCE, strategy update → STRATEGY_UPDATE, experiment recording → **EXPERIMENT_RESULT** when both A and B recorded; **run(context)** calls **analyze_performance** when `distribution_result` present |
| `service.yaml` | Version 0.4.0; added **analytics_agent** to agents list |
| `agents/__init__.py` | Export **AnalyticsAgent** |

## 3. Example performance event (CONTENT_PERFORMANCE)

```json
{
  "type": "CONTENT_PERFORMANCE",
  "payload": {
    "service_id": "influencer_engine",
    "avatar": "nova",
    "topic": "Best AI tools for creators in 2025",
    "performance_metrics": {
      "views": 12000,
      "likes": 840,
      "shares": 120,
      "watch_time": 45.5,
      "engagement_rate": 0.08,
      "follower_growth": 32,
      "duration_seconds": 60.0
    },
    "performance_score": 0.72,
    "experiment_id": "exp_a1b2c3d4e5f6",
    "variant": "B"
  }
}
```

## 4. Example experiment result event (EXPERIMENT_RESULT)

```json
{
  "type": "EXPERIMENT_RESULT",
  "payload": {
    "service_id": "influencer_engine",
    "experiment_id": "exp_a1b2c3d4e5f6",
    "winning_variant": "B",
    "score": 0.78,
    "score_a": 0.65,
    "score_b": 0.78
  }
}
```

Emitted when both variant A and B have been recorded via **record_experiment_result** (after each piece of content is analyzed). Opportunity Scanner can use this to detect profitable hooks/strategies.

## 5. Example strategy memory entry

Stored in Redis key `ie:strategy:{avatar_id}` (or in-memory fallback):

```json
{
  "avatar": "nova",
  "niche": "ai_tools",
  "best_hooks": [
    "5 AI tools nobody told you about",
    "These AI tools will change your life"
  ],
  "best_topics": [
    "Best AI tools for creators in 2025",
    "Free AI video editing tools"
  ],
  "best_platform": "youtube_shorts"
}
```

**Strategy feedback:** Before generating new jobs, the scheduler loads this via **get_strategy(avatar_id)** and uses **best_topics** to bias topic selection, **best_platform** to prefer platform, and **best_hooks** for A/B hook variants.

---

## Growth loop flow (Phase-4)

```
Trend discovery (TrendAgent)
      ↓
Strategy load (best_topics, best_platform, best_hooks)
      ↓
Content generation (A/B variants: experiment_id, variant, hook)
      ↓
Distribution (platform_target)
      ↓
Analytics collection (AnalyticsAgent.collect_performance)
      ↓
Performance score (calculate_performance_score)
      ↓
Strategy learning (update_from_performance → strategy memory)
      ↓
Experiment result (record_experiment_result → EXPERIMENT_RESULT when A+B done)
      ↓
Improved scheduling (next tick uses updated best_*)
```

All changes remain under `services/influencer_engine/`. No core modules modified.
