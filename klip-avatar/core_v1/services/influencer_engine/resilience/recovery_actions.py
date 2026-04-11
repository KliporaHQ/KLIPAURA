"""
Influencer Engine — Recovery actions.

Executes recovery for render, distribution, analytics failures.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .retry_manager import RetryManager
from .failure_analyzer import FailureAnalyzer


class RecoveryActions:
    """Performs recovery based on failure analysis."""

    def __init__(self):
        self.retry = RetryManager()
        self.analyzer = FailureAnalyzer()

    def on_failure(
        self,
        stage: str,
        error: Exception | str,
        context: Dict[str, Any],
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Analyze failure and enqueue for retry if recoverable.
        Returns analysis + enqueued job_id if enqueued.
        """
        analysis = self.analyzer.analyze(stage, error, context)
        result = {"analysis": analysis, "enqueued": False, "job_id": None}
        if not analysis.get("recoverable"):
            return result
        op = f"{stage}_retry"
        pl = payload or context.get("payload") or context
        attempt = (pl.get("_retry_attempt") or 0) + 1
        job_id = self.retry.enqueue(
            operation=op,
            payload={**pl, "_retry_attempt": attempt},
            failure_reason=analysis.get("error", ""),
            attempt=attempt,
        )
        result["enqueued"] = True
        result["job_id"] = job_id
        return result

    def process_retry_queue(self, handler: Any) -> int:
        """
        Process one item from retry queue with handler(job).
        Returns count processed.
        """
        processed = 0
        item = self.retry.dequeue()
        while item:
            if self.retry.should_retry(item):
                try:
                    handler(item)
                    processed += 1
                except Exception:
                    self.retry.enqueue(
                        item.get("operation", "unknown"),
                        item.get("payload", {}),
                        str(item.get("failure_reason", "")),
                        (item.get("attempt") or 0) + 1,
                    )
            item = self.retry.dequeue()
        return processed
