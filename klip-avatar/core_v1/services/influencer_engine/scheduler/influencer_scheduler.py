"""
Influencer Engine — InfluencerScheduler.

Loads avatar profiles, determines daily content targets, generates job payloads,
and schedules them via Service Manager. Emits scheduler and opportunity events.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import yaml

SERVICE_ID = "influencer_engine"
OPPORTUNITY_THRESHOLD = 0.9
REDIS_PREFIX = "ie:"
KEY_AVATAR_DAY = REDIS_PREFIX + "avatar:{}:{}"  # avatar_id, date (YYYY-MM-DD)

# Default A/B hook templates per niche (used when strategy memory has no best_hooks)
DEFAULT_HOOKS = {
    "ai_tools": (
        "These AI tools will change your life",
        "5 AI tools nobody told you about",
    ),
    "crypto": (
        "This could change how you see crypto",
        "What most traders still don't know",
    ),
}
DEFAULT_HOOKS_FALLBACK = ("This one tip changes everything", "What nobody told you about this")


def _redis():
    try:
        from klipaura_core.infrastructure.redis_client import get_redis_client
        return get_redis_client()
    except Exception:
        return None


def _avatar_profiles_path() -> str:
    base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "..", "config", "avatar_profiles.yaml")


def _load_avatar_profiles() -> Dict[str, Any]:
    path = _avatar_profiles_path()
    if not os.path.exists(path):
        # Fallback: from cwd (e.g. when server runs from repo root)
        cwd_path = os.path.join(os.getcwd(), "services", "influencer_engine", "config", "avatar_profiles.yaml")
        if os.path.exists(cwd_path):
            path = cwd_path
    if not os.path.exists(path):
        return {"avatars": {}}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {"avatars": {}}


def _load_avatar_intelligence_config() -> Dict[str, Any]:
    """Load avatar intelligence settings from config/live_ops.yaml (Phase 7). Safe defaults."""
    try:
        base = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(base, "..", "config", "live_ops.yaml")
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            return {
                "auto_create_avatars": bool(cfg.get("auto_create_avatars", False)),
                "max_active_avatars": int(cfg.get("max_active_avatars") or 5),
            }
    except Exception:
        pass
    return {"auto_create_avatars": False, "max_active_avatars": 5}


def _merged_avatars() -> Dict[str, Dict[str, Any]]:
    """Merge manual (avatar_profiles.yaml) + active store avatars. Keys = avatar_id (Phase 6)."""
    manual = _load_avatar_profiles().get("avatars") or {}
    out = {}
    for aid, prof in manual.items():
        p = dict(prof)
        if "avatar_id" not in p:
            p["avatar_id"] = aid
        out[aid] = p
    try:
        from ..avatar.avatar_store import list_avatars
        for store_prof in list_avatars(active_only=True):
            aid = store_prof.get("avatar_id") or ""
            if not aid:
                continue
            # Normalize to same shape as manual (tone, platforms, posting_frequency_per_day)
            p = dict(store_prof)
            if "platforms" not in p and "platforms" in store_prof:
                p["platforms"] = store_prof.get("platforms") or []
            out[aid] = p
    except Exception:
        pass
    try:
        from ..avatar.disk_profiles import list_disk_avatar_ids

        for aid in list_disk_avatar_ids():
            if aid not in out:
                out[aid] = {"avatar_id": aid, "from_disk_bundle": True}
    except Exception:
        pass
    return out


def _mission_control_avatar_row(avatar_id: str) -> Dict[str, Any]:
    """Redis avatar:custom:{id} from Mission Control dashboard (voice, I2V model, portrait URL, …)."""
    r = _redis()
    if not r:
        return {}
    try:
        row = r.get_json(f"avatar:custom:{avatar_id}")
        return dict(row) if isinstance(row, dict) else {}
    except Exception:
        return {}


def get_avatar_profile(avatar_id: str) -> Optional[Dict[str, Any]]:
    """Resolve profile by avatar_id from manual config, avatar store, and Mission Control Redis."""
    if not avatar_id:
        return None
    merged = _merged_avatars()
    prof = dict(merged.get(avatar_id) or {})
    try:
        from ..avatar.disk_profiles import load_disk_avatar_overlay

        disk = load_disk_avatar_overlay(avatar_id)
        for k, v in disk.items():
            if v is None:
                continue
            if isinstance(v, str) and not v.strip():
                continue
            if k not in prof or prof.get(k) in ("", None):
                prof[k] = v
    except Exception:
        pass
    mc = _mission_control_avatar_row(avatar_id)
    for k, v in mc.items():
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        prof[k] = v
    prof.setdefault("avatar_id", avatar_id)
    if not prof:
        return None
    return prof


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _get_scheduled_count(avatar_id: str) -> int:
    r = _redis()
    if not r:
        return 0
    key = KEY_AVATAR_DAY.format(avatar_id, _today_utc())
    try:
        return int(r.get(key) or 0)
    except (ValueError, TypeError):
        return 0


def _inc_scheduled_count(avatar_id: str) -> None:
    r = _redis()
    if not r:
        return
    key = KEY_AVATAR_DAY.format(avatar_id, _today_utc())
    r.incr(key)
    r.expire(key, 86400 * 2)  # keep 2 days


def _emit(event_type: str, payload: Dict[str, Any]) -> None:
    """Emit event to platform (Redis events + optional EventBus)."""
    try:
        from core.service_manager.utils.service_utils import event_publish
        event_publish(event_type, payload)
    except Exception:
        pass
    try:
        from core.service_manager.utils.event_bus_publisher import get_publisher
        pub = get_publisher()
        if pub is not None:
            pub.publish(event_type, payload, source=SERVICE_ID)
    except Exception:
        pass


def _schedule_job(service_manager: Any, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Schedule one job via Service Manager.
    service_manager can be an object with schedule_job(service_id, payload) or None.
    If None, use dispatch_task from core.
    """
    if service_manager is not None and hasattr(service_manager, "schedule_job"):
        return service_manager.schedule_job(SERVICE_ID, payload)
    try:
        from core.service_manager.core.task_dispatcher import dispatch_task
        out = dispatch_task(service_id=SERVICE_ID, task_type="default", payload=payload)
        return out
    except Exception:
        return None


class InfluencerScheduler:
    """
    Generates and schedules influencer_engine jobs from avatar profiles and trends.
    """

    def __init__(self, service_manager: Any = None):
        self.service_manager = service_manager

    def load_profiles(self) -> Dict[str, Any]:
        """Load avatar_profiles.yaml."""
        return _load_avatar_profiles()

    def pending_posts_for_avatar(self, avatar_id: str, profile: Dict[str, Any]) -> int:
        """Number of posts still to schedule today for this avatar."""
        frequency = int(profile.get("posting_frequency_per_day") or 0)
        scheduled = _get_scheduled_count(avatar_id)
        return max(0, frequency - scheduled)

    def generate_job_payload(
        self,
        avatar_id: str,
        profile: Dict[str, Any],
        topic: str,
        trend_score: float,
        platform_target: str,
        experiment_id: Optional[str] = None,
        variant: Optional[str] = None,
        hook: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build job payload for the pipeline. Optionally include experiment_id, variant, hook for A/B."""
        config = {
            "avatar_profile": avatar_id,
            "topic": topic,
            "trend_score": trend_score,
            "blueprint": {
                "platform_target": platform_target,
            },
        }
        if experiment_id:
            config["experiment_id"] = experiment_id
        if variant:
            config["variant"] = variant
        if hook:
            config["hook"] = hook
        return {
            "service_id": SERVICE_ID,
            "job_id": f"auto_job_{uuid.uuid4().hex[:16]}",
            "config": config,
        }

    def _get_ab_hooks(self, avatar_id: str, niche: str) -> tuple:
        """Return (hook_a, hook_b) for A/B testing; prefer strategy best_hooks."""
        try:
            from ..learning.strategy_memory import get_strategy
            strategy = get_strategy(avatar_id)
            hooks = list(strategy.get("best_hooks") or [])
            if len(hooks) >= 2:
                return (hooks[0], hooks[1])
            if len(hooks) == 1:
                return (hooks[0], DEFAULT_HOOKS.get(niche, DEFAULT_HOOKS_FALLBACK)[1])
        except Exception:
            pass
        return DEFAULT_HOOKS.get(niche, DEFAULT_HOOKS_FALLBACK)

    def _bias_topic_from_strategy(self, avatar_id: str, candidates: List[Dict], strategy: Dict[str, Any]) -> Dict:
        """If topic in best_topics, increase probability of reuse (pick first match or first candidate)."""
        best = list(strategy.get("best_topics") or [])
        for c in candidates:
            t = (c.get("topic") or "").strip()
            if t and t in best:
                return c
        return (candidates or [{"topic": "auto_discovered", "score": 0.5}])[0]

    def _platform_from_strategy(self, avatar_id: str, profile: Dict[str, Any], strategy: Dict[str, Any]) -> Optional[str]:
        """Prefer best_platform from strategy when available."""
        return (strategy.get("best_platform") or "").strip() or None

    def _adjust_pending_by_performance(
        self,
        pending: int,
        recent_performance: List[Dict[str, Any]],
        profile: Dict[str, Any],
    ) -> int:
        """Reduce posting frequency if recent performance is poor (avg score low)."""
        if not recent_performance:
            return pending
        scores = [float(r.get("score", 0) or 0) for r in recent_performance]
        avg = sum(scores) / len(scores) if scores else 0
        if avg < 0.3:
            return min(1, pending)
        if avg < 0.5:
            return max(1, pending // 2)
        return pending

    def run_tick(self) -> Dict[str, Any]:
        """
        One scheduler tick: load strategy memory, load profiles, determine pending,
        bias topic/platform from strategy, generate jobs (with A/B variants when pending >= 2).
        """
        from ..agents.trend_agent import TrendAgent
        from ..agents.distribution_agent import DistributionAgent
        try:
            from ..learning.strategy_memory import get_strategy
        except Exception:
            get_strategy = lambda _: {}
        try:
            from ..analytics.performance_store import get_recent_metrics
        except Exception:
            get_recent_metrics = lambda _a, limit=20: []

        avatars = _merged_avatars()
        avatar_intelligence = _load_avatar_intelligence_config()
        auto_create = avatar_intelligence.get("auto_create_avatars", False)
        max_active = avatar_intelligence.get("max_active_avatars", 5)
        trend_agent_instance = TrendAgent()

        jobs_generated = 0
        avatar_jobs: Dict[str, int] = {}
        events_emitted: List[str] = []
        suggested_or_created_niches_this_tick: set = set()

        _emit("SCHEDULER_TICK", {"service_id": SERVICE_ID, "timestamp": datetime.now(timezone.utc).isoformat()})
        events_emitted.append("SCHEDULER_TICK")

        for avatar_id, profile in avatars.items():
            strategy = get_strategy(avatar_id)
            recent_performance = get_recent_metrics(avatar_id, limit=20)
            pending = self.pending_posts_for_avatar(avatar_id, profile)
            if pending <= 0:
                continue
            pending = self._adjust_pending_by_performance(pending, recent_performance, profile)
            if pending <= 0:
                continue
            niche = (profile.get("niche") or "").strip() or "general"
            candidates = trend_agent_instance.discover_trends_for_niche(niche)
            topic_row = self._bias_topic_from_strategy(avatar_id, candidates or [], strategy)
            topic = topic_row.get("topic", "auto_discovered")
            score = float(topic_row.get("score", 0.5))

            if score > OPPORTUNITY_THRESHOLD:
                _emit(
                    "OPPORTUNITY_EVENT",
                    {
                        "service_id": SERVICE_ID,
                        "avatar": avatar_id,
                        "topic": topic,
                        "trend_score": score,
                    },
                )
                events_emitted.append("OPPORTUNITY_EVENT")
                # Phase 3 & 8: high opportunity + niche not saturated -> suggest or create avatar
                if niche not in suggested_or_created_niches_this_tick and len(avatars) < max_active:
                    suggested_or_created_niches_this_tick.add(niche)
                    opportunity = {
                        "niche": niche,
                        "trend_topics": [topic],
                        "audience": "general",
                        "trend_score": score,
                    }
                    if auto_create:
                        try:
                            from ..avatar.avatar_generator import generate_avatar_profile
                            from ..avatar.avatar_store import save_avatar
                            new_profile = generate_avatar_profile(opportunity)
                            new_id = save_avatar(new_profile)
                            _emit("AVATAR_CREATED", {
                                "avatar_id": new_id,
                                "niche": niche,
                                "reason": "high trend velocity",
                                "confidence": round(score, 2),
                            })
                            events_emitted.append("AVATAR_CREATED")
                            avatars = _merged_avatars()
                        except Exception:
                            pass
                    else:
                        _emit("AVATAR_SUGGESTION", {
                            "niche": niche,
                            "reason": "high trend velocity",
                            "confidence": round(score, 2),
                        })
                        events_emitted.append("AVATAR_SUGGESTION")

            platform = self._platform_from_strategy(avatar_id, profile, strategy) or DistributionAgent.optimize_platform_target(topic, profile)
            hook_a, hook_b = self._get_ab_hooks(avatar_id, niche)

            scheduled_this_avatar = 0
            slot = 0
            while pending > 0 and slot < 20:
                # A/B: when at least 2 slots, create one experiment with variant A and B
                if pending >= 2:
                    experiment_id = f"exp_{uuid.uuid4().hex[:12]}"
                    ab_scheduled = 0
                    for variant, hook in (("A", hook_a), ("B", hook_b)):
                        payload = self.generate_job_payload(
                            avatar_id, profile, topic, score, platform,
                            experiment_id=experiment_id, variant=variant, hook=hook,
                        )
                        if _schedule_job(self.service_manager, payload):
                            jobs_generated += 1
                            scheduled_this_avatar += 1
                            ab_scheduled += 1
                            _inc_scheduled_count(avatar_id)
                    pending -= ab_scheduled
                else:
                    payload = self.generate_job_payload(avatar_id, profile, topic, score, platform, hook=hook_a)
                    if _schedule_job(self.service_manager, payload):
                        jobs_generated += 1
                        scheduled_this_avatar += 1
                        _inc_scheduled_count(avatar_id)
                        pending -= 1
                slot += 1

            if scheduled_this_avatar > 0:
                avatar_jobs[avatar_id] = scheduled_this_avatar
                _emit(
                    "AVATAR_CONTENT_TARGET",
                    {"avatar": avatar_id, "jobs_generated": scheduled_this_avatar},
                )
                events_emitted.append("AVATAR_CONTENT_TARGET")

        _emit(
            "JOBS_GENERATED",
            {"service_id": SERVICE_ID, "jobs_generated": jobs_generated, "avatars": avatar_jobs},
        )
        events_emitted.append("JOBS_GENERATED")

        return {"avatars": avatar_jobs, "jobs_generated": jobs_generated, "events_emitted": events_emitted}
