"""Stub for legacy queue_factory import — returns a no-op queue wrapper."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class _StubQueue:
    """Minimal stand-in so callers don't crash; logs instead of enqueuing."""

    def enqueue(self, *args, **kwargs):
        logger.warning("StubQueue.enqueue called — klipaura_core.queue not configured")

    def dequeue(self, *args, **kwargs):
        return None

    def __len__(self):
        return 0


def get_queue(name: str = "default", **kwargs):
    logger.warning("klipaura_core queue_factory.get_queue(%r) — returning stub", name)
    return _StubQueue()
