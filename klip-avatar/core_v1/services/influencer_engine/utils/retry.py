"""
Influencer Engine — Simple retry helper for API calls (rate limits, network blips).
No external dependencies. Use for WaveSpeed and Groq/LLM calls.
"""

from __future__ import annotations

import time
from typing import Any, Callable, TypeVar

T = TypeVar("T")

DEFAULT_ATTEMPTS = 3
DEFAULT_BACKOFF_SEC = 2


def with_retry(
    fn: Callable[[], T],
    max_attempts: int = DEFAULT_ATTEMPTS,
    backoff_sec: float = DEFAULT_BACKOFF_SEC,
    retry_exceptions: tuple = (Exception,),
) -> T:
    """
    Call fn(); on failure retry up to max_attempts with backoff_sec sleep.
    Raises the last exception if all attempts fail.
    """
    last: Any = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except retry_exceptions as e:
            last = e
            if attempt + 1 < max_attempts:
                time.sleep(backoff_sec)
            else:
                raise
    if last is not None:
        raise last
    raise RuntimeError("with_retry: no result")
