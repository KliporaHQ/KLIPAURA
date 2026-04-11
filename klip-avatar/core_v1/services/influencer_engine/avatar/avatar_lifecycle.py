"""
Influencer Engine — Avatar Lifecycle Manager.

Scale UP / Maintain / Kill avatars based on compute_avatar_score().
- score > 0.7: increase posting_frequency, allocate more jobs
- 0.4 <= score <= 0.7: maintain
- score < 0.3: deactivate avatar (pause/kill)
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .avatar_performance import compute_avatar_score
from .avatar_store import get_avatar, list_avatars, update_avatar, deactivate_avatar

SERVICE_ID = "influencer_engine"
SCALE_UP_THRESHOLD = 0.7
MAINTAIN_LOW = 0.4
KILL_THRESHOLD = 0.3
MAX_POSTING_PER_DAY = 8
MIN_POSTING_PER_DAY = 1


def _emit(event_type: str, payload: Dict[str, Any]) -> None:
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


def run_lifecycle_tick() -> Dict[str, Any]:
    """
    One lifecycle tick: for each active (store) avatar, compute score and
    apply scale up / maintain / deactivate. Returns summary of actions.
    """
    actions: List[str] = []
    scaled: List[str] = []
    maintained: List[str] = []
    deactivated: List[str] = []

    avatars = list_avatars(active_only=True)
    for profile in avatars:
        avatar_id = profile.get("avatar_id") or ""
        if not avatar_id:
            continue
        # Only manage avatars that came from the store (auto-generated)
        if profile.get("source") != "avatar_generator":
            continue

        score = compute_avatar_score(avatar_id)
        current_freq = int(profile.get("posting_frequency_per_day") or 1)

        if score >= SCALE_UP_THRESHOLD:
            if current_freq < MAX_POSTING_PER_DAY:
                new_freq = min(MAX_POSTING_PER_DAY, current_freq + 1)
                update_avatar(avatar_id, {"posting_frequency_per_day": new_freq})
                scaled.append(avatar_id)
                actions.append(f"scale_up:{avatar_id}:{new_freq}")
        elif score < KILL_THRESHOLD:
            deactivate_avatar(avatar_id)
            deactivated.append(avatar_id)
            actions.append(f"deactivate:{avatar_id}")
            _emit("AVATAR_DEACTIVATED", {
                "avatar_id": avatar_id,
                "reason": "low_performance",
                "score": score,
            })
        else:
            maintained.append(avatar_id)

    return {
        "scaled_up": scaled,
        "maintained": maintained,
        "deactivated": deactivated,
        "actions": actions,
    }
