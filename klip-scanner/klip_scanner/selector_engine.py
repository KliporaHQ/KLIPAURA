from __future__ import annotations

import os
from typing import Any, Dict, List

from infrastructure.db import get_session
from infrastructure.db_models import Opportunity, OpportunityScore


def calculate_opportunity_score(opportunity: Opportunity, geo_target: str = "AE") -> Dict[str, Any]:
    """
    Calculate comprehensive opportunity score with UAE compliance penalty.
    Returns score components and final tier classification.
    """
    base_scores = {
        "momentum_score": opportunity.trend_score or 0,
        "audience_fit_score": 0,  # Will be calculated from metadata
        "payout_score": opportunity.affiliate_score or 0,
        "content_ease_score": 0,  # Will be calculated from content angle
        "competition_score": 0,   # Will be calculated from market data
        "risk_score": 0,          # Will be calculated from compliance
    }
    
    # Extract additional scoring factors from raw data
    raw_data = opportunity.raw or {}
    
    # Audience fit score (20% weight) - based on niche and market
    niche = raw_data.get("niche", "").lower()
    if niche in ["beauty", "fashion", "electronics", "home", "kitchen"]:
        base_scores["audience_fit_score"] = 80
    elif niche in ["productivity", "software", "education"]:
        base_scores["audience_fit_score"] = 70
    else:
        base_scores["audience_fit_score"] = 50
    
    # Content ease score (10% weight) - based on content angle
    content_angle = opportunity.content_angle or ""
    if content_angle and len(content_angle) > 50:
        base_scores["content_ease_score"] = 70
    else:
        base_scores["content_ease_score"] = 40
    
    # Competition score (10% weight) - inverse of market saturation
    category = raw_data.get("category", "").lower()
    high_competition_categories = ["weight_loss", "crypto", "dating", "gambling"]
    if category in high_competition_categories:
        base_scores["competition_score"] = 30
    else:
        base_scores["competition_score"] = 70
    
    # Risk score (10% weight) - based on compliance and market factors
    compliance_data = opportunity.compliance_data or {}
    compliance_score = compliance_data.get("compliance_score", 100)
    
    if compliance_score < 80:
        base_scores["risk_score"] = 20  # High risk
    elif compliance_score < 100:
        base_scores["risk_score"] = 50  # Medium risk
    else:
        base_scores["risk_score"] = 80  # Low risk
    
    # UAE compliance penalty (-50% if risky)
    if opportunity.state == "blocked_compliance":
        base_scores["risk_score"] = 0  # Blocked
    elif compliance_score < 80:
        base_scores["risk_score"] = int(base_scores["risk_score"] * 0.5)  # Penalty
    
    # Calculate weighted total score
    weights = {
        "momentum_score": 0.30,
        "audience_fit_score": 0.20,
        "payout_score": 0.20,
        "content_ease_score": 0.10,
        "competition_score": 0.10,
        "risk_score": 0.10,
    }
    
    total_score = sum(base_scores[key] * weights[key] for key in weights)
    total_score = max(0, min(100, int(total_score)))  # Clamp to 0-100
    
    # Determine tier
    if total_score >= 80:
        tier = "A"
    elif total_score >= 60:
        tier = "B"
    elif total_score >= 40:
        tier = "C"
    else:
        tier = "D"
    
    return {
        "opportunity_id": str(opportunity.id),
        "scores": base_scores,
        "weights": weights,
        "total_score": total_score,
        "tier": tier,
        "compliance_score": compliance_score,
        "geo_target": geo_target,
        "explanation": {
            "momentum": f"Trend score: {base_scores['momentum_score']}/100",
            "audience": f"Niche fit: {base_scores['audience_fit_score']}/100",
            "payout": f"Commission potential: {base_scores['payout_score']}/100",
            "ease": f"Content difficulty: {base_scores['content_ease_score']}/100",
            "competition": f"Market saturation: {base_scores['competition_score']}/100",
            "risk": f"Risk assessment: {base_scores['risk_score']}/100",
        }
    }


def run_selector_engine(limit: int = 50, geo_target: str = "AE") -> Dict[str, Any]:
    """
    Run the complete selector engine:
    1. Load opportunities from Postgres
    2. Calculate scores for each
    3. Persist scores to database
    4. Return top opportunities by tier
    """
    with get_session() as sess:
        # Load unblocked opportunities
        opportunities = sess.query(Opportunity).filter(
            Opportunity.state != "blocked_compliance"
        ).limit(limit).all()
        
        scored_opportunities = []
        for opp in opportunities:
            score_data = calculate_opportunity_score(opp, geo_target)
            
            # Check if score already exists
            existing_score = sess.query(OpportunityScore).filter(
                OpportunityScore.opportunity_id == opp.id
            ).first()
            
            if existing_score:
                # Update existing score
                existing_score.momentum_score = score_data["scores"]["momentum_score"]
                existing_score.audience_fit_score = score_data["scores"]["audience_fit_score"]
                existing_score.payout_score = score_data["scores"]["payout_score"]
                existing_score.content_ease_score = score_data["scores"]["content_ease_score"]
                existing_score.competition_score = score_data["scores"]["competition_score"]
                existing_score.risk_score = score_data["scores"]["risk_score"]
                existing_score.total_score = score_data["total_score"]
                existing_score.tier = score_data["tier"]
                existing_score.explain = score_data["explanation"]
            else:
                # Create new score record
                score_record = OpportunityScore(
                    opportunity_id=opp.id,
                    momentum_score=score_data["scores"]["momentum_score"],
                    audience_fit_score=score_data["scores"]["audience_fit_score"],
                    payout_score=score_data["scores"]["payout_score"],
                    content_ease_score=score_data["scores"]["content_ease_score"],
                    competition_score=score_data["scores"]["competition_score"],
                    risk_score=score_data["scores"]["risk_score"],
                    total_score=score_data["total_score"],
                    tier=score_data["tier"],
                    explain=score_data["explanation"]
                )
                sess.add(score_record)
            
            scored_opportunities.append({
                "opportunity": opp,
                "score": score_data
            })
        
        sess.commit()
        
        # Group by tier for response
        by_tier = {"A": [], "B": [], "C": [], "D": []}
        for item in scored_opportunities:
            tier = item["score"]["tier"]
            by_tier[tier].append({
                "opportunity_id": str(item["opportunity"].id),
                "title": item["opportunity"].title,
                "source": item["opportunity"].source,
                "total_score": item["score"]["total_score"],
                "tier": tier,
                "compliance_score": item["score"]["compliance_score"],
                "explanation": item["score"]["explanation"]
            })
        
        # Sort each tier by score (descending)
        for tier in by_tier:
            by_tier[tier].sort(key=lambda x: x["total_score"], reverse=True)
        
        return {
            "processed": len(opportunities),
            "geo_target": geo_target,
            "by_tier": by_tier,
            "top_a_tier": by_tier["A"][:5],  # Top 5 A-tier for content generation
            "summary": {
                "tier_a": len(by_tier["A"]),
                "tier_b": len(by_tier["B"]),
                "tier_c": len(by_tier["C"]),
                "tier_d": len(by_tier["D"]),
                "avg_compliance_score": sum(item["score"]["compliance_score"] for item in scored_opportunities) / len(scored_opportunities) if scored_opportunities else 0
            }
        }


def get_top_opportunities(tier: str = "A", limit: int = 10, geo_target: str = "AE") -> List[Dict[str, Any]]:
    """
    Get top scored opportunities by tier for content generation.
    """
    with get_session() as sess:
        scores = sess.query(OpportunityScore, Opportunity).join(
            Opportunity, OpportunityScore.opportunity_id == Opportunity.id
        ).filter(
            OpportunityScore.tier == tier.upper(),
            Opportunity.state != "blocked_compliance"
        ).order_by(OpportunityScore.total_score.desc()).limit(limit).all()
        
        results = []
        for score, opportunity in scores:
            results.append({
                "opportunity_id": str(opportunity.id),
                "title": opportunity.title,
                "description": opportunity.description,
                "url": opportunity.url,
                "source": opportunity.source,
                "total_score": score.total_score,
                "tier": score.tier,
                "scores": {
                    "momentum": score.momentum_score,
                    "audience": score.audience_fit_score,
                    "payout": score.payout_score,
                    "ease": score.content_ease_score,
                    "competition": score.competition_score,
                    "risk": score.risk_score,
                },
                "explanation": score.explain,
                "raw": opportunity.raw,
            })
        
        return results
