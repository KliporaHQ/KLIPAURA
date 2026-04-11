"""
Influencer Engine — Failure analyzer.

Classifies render, distribution, analytics failures for recovery.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

FAILURE_CATEGORIES = ("render", "distribution", "analytics", "validation", "unknown")


class FailureAnalyzer:
    """Analyzes failure context and suggests recovery."""

    def analyze(
        self,
        stage: str,
        error: Exception | str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Categorize failure and return category, recoverable, suggested_action.
        """
        err_str = str(error) if isinstance(error, Exception) else (error or "")
        context = context or {}
        category = self._categorize(stage, err_str, context)
        recoverable = category in ("render", "distribution", "analytics")
        suggested = self._suggested_action(category, err_str)
        return {
            "category": category,
            "stage": stage,
            "error": err_str,
            "recoverable": recoverable,
            "suggested_action": suggested,
            "context_keys": list(context.keys()),
        }

    def _categorize(self, stage: str, err_str: str, context: Dict[str, Any]) -> str:
        stage_lower = (stage or "").lower()
        err_lower = err_str.lower()
        if "render" in stage_lower or "avatar" in stage_lower or "voice" in stage_lower or "video" in stage_lower:
            return "render"
        if "publish" in stage_lower or "distribution" in stage_lower or "upload" in err_lower:
            return "distribution"
        if "analytics" in stage_lower or "metrics" in stage_lower:
            return "analytics"
        if "validat" in stage_lower or "compliance" in err_lower:
            return "validation"
        return "unknown"

    def _suggested_action(self, category: str, err_str: str) -> str:
        if category == "render":
            return "retry_render"
        if category == "distribution":
            return "retry_publish"
        if category == "analytics":
            return "retry_analytics"
        if category == "validation":
            return "fix_content"
        return "manual_review"
