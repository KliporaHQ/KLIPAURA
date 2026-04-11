"""
Influencer Engine — Retry manager.

Handles render failures, distribution failures, analytics failures via retry_queue.
"""

from __future__ import annotations

import json
import time
import uuid
from collections import deque
from typing import Any, Callable, Dict, List, Optional

REDIS_PREFIX = "ie:retry:"
KEY_QUEUE = REDIS_PREFIX + "queue"
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 60

# In-memory fallback when Redis unavailable
_retry_queue: deque = deque()
_retry_queue_max = 1000


def _redis():
    try:
        from klipaura_core.infrastructure.redis_client import get_redis_client
        return get_redis_client()
    except Exception:
        return None


class RetryManager:
    """Enqueues failed operations for retry; processes retry queue."""

    def enqueue(
        self,
        operation: str,
        payload: Dict[str, Any],
        failure_reason: str = "",
        attempt: int = 1,
    ) -> str:
        """Enqueue item for retry. Returns job_id."""
        job_id = str(uuid.uuid4())
        item = {
            "job_id": job_id,
            "operation": operation,
            "payload": payload,
            "failure_reason": failure_reason,
            "attempt": attempt,
            "created_at": time.time(),
        }
        r = _redis()
        if r:
            try:
                r.lpush(KEY_QUEUE, json.dumps(item))
                r.ltrim(KEY_QUEUE, 0, 9999)
                return job_id
            except Exception:
                pass
        if len(_retry_queue) < _retry_queue_max:
            _retry_queue.append(item)
        return job_id

    def dequeue(self) -> Optional[Dict[str, Any]]:
        """Pop one item from retry queue."""
        r = _redis()
        if r:
            try:
                raw = r.rpop(KEY_QUEUE)
                if raw:
                    return json.loads(raw.decode() if isinstance(raw, bytes) else raw)
            except Exception:
                pass
        if _retry_queue:
            return _retry_queue.popleft()
        return None

    def size(self) -> int:
        r = _redis()
        if r:
            try:
                return r.llen(KEY_QUEUE)
            except Exception:
                pass
        return len(_retry_queue)

    def should_retry(self, item: Dict[str, Any]) -> bool:
        return (item.get("attempt") or 0) < MAX_RETRIES


def retry_queue(operation: str, payload: Dict[str, Any], failure_reason: str = "", attempt: int = 1) -> str:
    """Module-level enqueue."""
    return RetryManager().enqueue(operation, payload, failure_reason, attempt)
