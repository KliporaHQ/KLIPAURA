from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class ComplianceResult:
    passed: bool
    reason: str
    matched_category: str


class CompliancePolicy:
    """Rules-first compliance gate with default-deny behavior."""

    def __init__(self, blocked_categories: Iterable[str] | None = None) -> None:
        defaults = {
            "adult",
            "gambling",
            "alcohol",
            "tobacco",
            "vape",
            "weapons",
            "crypto_high_risk",
            "illegal",
            "unknown",
        }
        self._blocked = {c.strip().lower() for c in (blocked_categories or defaults) if str(c).strip()}

    def evaluate(self, category: str | None) -> ComplianceResult:
        raw = (category or "").strip().lower()
        if not raw:
            return ComplianceResult(
                passed=False,
                reason="Missing category (default deny)",
                matched_category="unknown",
            )
        if raw in self._blocked:
            return ComplianceResult(
                passed=False,
                reason=f"Category '{raw}' blocked by compliance policy",
                matched_category=raw,
            )
        return ComplianceResult(passed=True, reason="Compliance pass", matched_category=raw)
