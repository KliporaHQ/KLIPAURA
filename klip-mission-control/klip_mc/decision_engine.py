from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from klip_mc.compliance_policy import CompliancePolicy


@dataclass(frozen=True)
class DecisionConfig:
    auto_approve_threshold: float = 0.8
    manual_review_threshold: float = 0.5
    pregen_hitl_required: bool = True


@dataclass(frozen=True)
class Candidate:
    avatar_id: str
    category: str
    affiliate_tracking_id: str
    has_crosspost_risk: bool
    estimated_cost: float
    remaining_budget: float
    trend_score: float
    commission_rate: float


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def evaluate_candidate(
    candidate: Candidate,
    config: DecisionConfig,
    compliance_policy: CompliancePolicy,
) -> dict[str, Any]:
    compliance = compliance_policy.evaluate(candidate.category)
    hard_gates: dict[str, dict[str, Any]] = {
        "compliance_fail": {"blocked": not compliance.passed, "reason": compliance.reason},
        "missing_affiliate_tracking": {
            "blocked": not bool(candidate.affiliate_tracking_id.strip()),
            "reason": "Missing affiliate tracking id/tag",
        },
        "cap_block": {
            "blocked": candidate.estimated_cost > max(0.0, candidate.remaining_budget),
            "reason": (
                f"Estimated cost {candidate.estimated_cost:.2f} exceeds remaining budget "
                f"{candidate.remaining_budget:.2f}"
            ),
        },
        "crosspost_risk_placeholder": {
            "blocked": bool(candidate.has_crosspost_risk),
            "reason": "Cross-post risk placeholder gate triggered",
        },
    }
    blocked_reasons = [v["reason"] for v in hard_gates.values() if v["blocked"]]
    if blocked_reasons:
        return {
            "route": "AUTO_REJECT",
            "final_score": 0.0,
            "component_scores": {
                "commission_score": 0.0,
                "trend_score": 0.0,
                "budget_score": 0.0,
            },
            "hard_gates": hard_gates,
            "explainability": {
                "reason": "Hard gate failed",
                "blocked_reasons": blocked_reasons,
                "weights": {"commission_score": 0.5, "trend_score": 0.35, "budget_score": 0.15},
            },
        }

    commission_score = _clamp01(candidate.commission_rate / 20.0)
    trend_score = _clamp01(candidate.trend_score)
    budget_score = _clamp01(
        1.0 if candidate.estimated_cost <= 0 else max(0.0, (candidate.remaining_budget - candidate.estimated_cost) / max(candidate.remaining_budget, 1.0))
    )
    final_score = _clamp01((commission_score * 0.5) + (trend_score * 0.35) + (budget_score * 0.15))

    route = "AUTO_REJECT"
    if config.pregen_hitl_required:
        route = "MANUAL_REVIEW"
    elif final_score >= config.auto_approve_threshold:
        route = "AUTO_APPROVE"
    elif final_score >= config.manual_review_threshold:
        route = "MANUAL_REVIEW"

    return {
        "route": route,
        "final_score": final_score,
        "component_scores": {
            "commission_score": round(commission_score, 4),
            "trend_score": round(trend_score, 4),
            "budget_score": round(budget_score, 4),
        },
        "hard_gates": hard_gates,
        "explainability": {
            "reason": "Threshold routing decision",
            "blocked_reasons": [],
            "weights": {"commission_score": 0.5, "trend_score": 0.35, "budget_score": 0.15},
            "thresholds": {
                "auto_approve_threshold": config.auto_approve_threshold,
                "manual_review_threshold": config.manual_review_threshold,
                "pregen_hitl_required": config.pregen_hitl_required,
            },
        },
    }
