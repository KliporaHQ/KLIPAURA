# Avatar Intelligence — Examples

## 1. Example generated avatar (Phase 1)

**Input (opportunity):**
```json
{
  "niche": "ai_tools",
  "trend_topics": ["Best AI tools for creators in 2025"],
  "audience": "young entrepreneurs",
  "trend_score": 0.92
}
```

**Output (`generate_avatar_profile(opportunity)`):**
```json
{
  "avatar_id": "ai_tools_a1b2c3",
  "niche": "ai_tools",
  "tone": "futuristic educator",
  "persona": {
    "tone": "futuristic educator",
    "style": "fast-paced, high-energy",
    "hook_style": "curiosity-driven"
  },
  "platforms": ["youtube_shorts", "tiktok"],
  "posting_frequency_per_day": 3,
  "source": "avatar_generator",
  "trend_topics": ["Best AI tools for creators in 2025"],
  "audience": "young entrepreneurs"
}
```

---

## 2. Example avatar suggestion event (Phase 8)

When `auto_create_avatars: false` and trend_score > 0.9, niche not saturated:

```json
{
  "type": "AVATAR_SUGGESTION",
  "timestamp": "2026-03-17T12:00:00.000000Z",
  "payload": {
    "niche": "ai_tools",
    "reason": "high trend velocity",
    "confidence": 0.92
  }
}
```

---

## 3. Example avatar lifecycle decision (Phase 5)

**Scale UP** (score > 0.7):  
`run_lifecycle_tick()` increases `posting_frequency_per_day` by 1 (cap 8).  
No event; next scheduler tick uses updated frequency.

**Maintain** (0.4 ≤ score ≤ 0.7):  
No change.

**Kill / Pause** (score < 0.3):  
`deactivate_avatar(avatar_id)`; emit:

```json
{
  "type": "AVATAR_DEACTIVATED",
  "payload": {
    "avatar_id": "ai_tools_a1b2c3",
    "reason": "low_performance",
    "score": 0.28
  }
}
```

---

## 4. Running lifecycle

Call from a cron or after scheduler tick:

```python
from services.influencer_engine.avatar import run_lifecycle_tick
result = run_lifecycle_tick()
# result: {"scaled_up": [...], "maintained": [...], "deactivated": [...], "actions": [...]}
```
