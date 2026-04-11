"""
Influencer Engine — Worker pool.

start_worker_pool(service_manager, num_workers=5) using threading.
"""

from __future__ import annotations

import os
import sys
import threading
from typing import Any, List, Optional

def _bootstrap() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(here)
    for p in (repo_root, here):
        if p not in sys.path:
            sys.path.insert(0, p)


def start_worker_pool(
    service_manager: Any = None,
    num_workers: int = 5,
    queue: Any = None,
    stop_flag: Any = None,
) -> List[threading.Thread]:
    """
    Start num_workers threads, each running start_worker.
    Returns list of started threads. Caller can join() or use stop_flag to stop.
    """
    _bootstrap()
    from worker.worker import start_worker

    try:
        from klipaura_core.infrastructure.queue.queue_factory import get_queue
        if queue is None:
            queue = get_queue("job")
    except Exception:
        queue = None

    threads: List[threading.Thread] = []
    for i in range(num_workers):
        t = threading.Thread(
            target=start_worker,
            kwargs={
                "service_manager": service_manager,
                "worker_id": f"ie_worker_{i}",
                "queue": queue,
                "execute_fn": None,
                "stop_flag": stop_flag,
            },
            daemon=True,
        )
        t.start()
        threads.append(t)
    return threads
