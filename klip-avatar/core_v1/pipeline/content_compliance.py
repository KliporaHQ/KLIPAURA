from __future__ import annotations

import os
from typing import Any, Dict

from klip_scanner.uae_compliance import check_compliance_violations, generate_compliance_prompt


def apply_content_compliance(
    script: str, 
    product_title: str, 
    product_description: str,
    geo_target: str = "AE"
) -> Dict[str, Any]:
    """
    Apply UAE compliance checks to generated content before rendering.
    Returns compliance assessment with required modifications.
    """
    # Check script for compliance violations
    compliance = check_compliance_violations(
        product_title, 
        product_description + " " + script, 
        category="affiliate_content",
        geo_target=geo_target
    )
    
    # If auto-blocked, return failure
    if compliance["auto_block"]:
        return {
            "compliant": False,
            "auto_blocked": True,
            "reason": f"Content blocked: {', '.join(compliance['risk_flags'])}",
            "required_disclosure": compliance["required_disclosure"],
            "violations": compliance["violations"],
        }
    
    # If high risk, flag for manual review
    if compliance["compliance_score"] < 80:
        return {
            "compliant": False,
            "requires_manual_review": True,
            "reason": f"Content requires manual review: {', '.join(compliance['risk_flags'])}",
            "required_disclosure": compliance["required_disclosure"],
            "violations": compliance["violations"],
            "compliance_score": compliance["compliance_score"],
        }
    
    # Content is compliant, add required disclosure
    required_disclosure = compliance["required_disclosure"]
    modified_script = script
    
    # Ensure disclosure is included naturally
    if required_disclosure and required_disclosure not in script:
        # Add disclosure at the end of CTA section
        if "cta" in script.lower() or "link" in script.lower():
            modified_script = script.rstrip() + f"\n\n{required_disclosure}"
        else:
            modified_script = script.rstrip() + f"\n\n{required_disclosure}"
    
    return {
        "compliant": True,
        "script": modified_script,
        "required_disclosure": required_disclosure,
        "compliance_score": compliance["compliance_score"],
        "violations": compliance["violations"],
    }


def add_compliance_to_ugc_prompt(base_prompt: str, geo_target: str = "AE") -> str:
    """
    Enhance UGC script generation prompt with compliance requirements.
    """
    compliance_prompt = generate_compliance_prompt(base_prompt, geo_target)
    
    # Add specific script generation rules
    script_rules = """

SCRIPT GENERATION RULES:
1. Focus on product features and benefits
2. Avoid guaranteed income claims ("you'll earn $X/day")
3. No medical efficacy claims ("this will cure X")
4. No "overnight success" language
5. Include required disclosure naturally in CTA
6. Use authentic, conversational tone
7. Keep claims realistic and achievable

STRUCTURE:
- Hook (attention-grabbing but realistic)
- Product introduction (features, not miracles)
- Personal experience (honest, not exaggerated)
- Call-to-action (with required disclosure)
"""
    
    return compliance_prompt + script_rules


def validate_compliance_before_rendering(
    content_data: Dict[str, Any], 
    geo_target: str = "AE"
) -> Dict[str, Any]:
    """
    Final compliance check before video rendering.
    Called by ugc_pipeline before final output.
    """
    title = content_data.get("title", "")
    script = content_data.get("script", "")
    description = content_data.get("description", "")
    
    compliance = apply_content_compliance(script, title, description, geo_target)
    
    if not compliance["compliant"]:
        return {
            "can_render": False,
            "reason": compliance["reason"],
            "auto_blocked": compliance.get("auto_blocked", False),
            "requires_manual_review": compliance.get("requires_manual_review", False),
        }
    
    # Update content with compliant script
    content_data["script"] = compliance["script"]
    content_data["required_disclosure"] = compliance["required_disclosure"]
    content_data["compliance_score"] = compliance["compliance_score"]
    
    return {
        "can_render": True,
        "content_data": content_data,
        "compliance_score": compliance["compliance_score"],
        "violations": compliance["violations"],
    }


def log_compliance_decision(
    content_id: str,
    geo_target: str,
    compliance_score: int,
    violations: list,
    auto_blocked: bool,
    requires_manual_review: bool
) -> None:
    """
    Log compliance decisions for audit trail.
    """
    log_entry = {
        "content_id": content_id,
        "geo_target": geo_target,
        "compliance_score": compliance_score,
        "violations": violations,
        "auto_blocked": auto_blocked,
        "requires_manual_review": requires_manual_review,
        "timestamp": os.getenv("COMPLIANCE_LOG_TIMESTAMP", "2026-04-03T12:00:00Z"),
    }
    
    # In production, this would go to a compliance audit table
    print(f"[COMPLIANCE] {log_entry}", flush=True)
