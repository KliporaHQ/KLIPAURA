from __future__ import annotations

import re
from typing import Any, Dict, List

# UAE Legal Compliance Configuration
UAE_BANNED_CATEGORIES = {
    "adult_content": ["adult", "porn", "xxx", "erotic", "nsfw"],
    "gambling": ["gambling", "casino", "bet", "poker", "lottery"],
    "alcohol": ["alcohol", "beer", "wine", "spirits", "liquor"],
    "tobacco": ["tobacco", "cigarette", "vape", "shisha", "hookah"],
    "dating": ["dating", "escort", "adult chat", "cam"],
    "vpn": ["vpn", "virtual private network", "unblock"],
    "crypto": ["bitcoin", "cryptocurrency", "crypto trading", "blockchain"],
    "forex": ["forex", "binary options", "trading signals", "leverage trading"],
    "medical_claims": ["weight loss", "miracle cure", "medical breakthrough", "supplement"],
    "financial_services": ["loan", "credit repair", "debt relief", "investment scheme"],
}

UAE_HIGH_RISK_CATEGORIES = {
    "weight_loss": ["diet pill", "fat burner", "rapid weight loss"],
    "mlm": ["multi-level marketing", "pyramid scheme", "downline"],
    "get_rich_quick": ["get rich", "overnight success", "quick money"],
    "real_estate": ["real estate investment", "property flipping", "rental income"],
}

UAE_SAFE_CATEGORIES = {
    "productivity": ["productivity", "saas", "software", "tool"],
    "ecommerce": ["gadgets", "electronics", "home", "kitchen", "fashion"],
    "education": ["course", "tutorial", "coding", "design", "business"],
    "consumer": ["consumer electronics", "accessories", "appliances"],
}

# Geo-specific compliance rules
GEO_COMPLIANCE_RULES = {
    "AE": {
        "banned": UAE_BANNED_CATEGORIES,
        "high_risk": UAE_HIGH_RISK_CATEGORIES,
        "safe": UAE_SAFE_CATEGORIES,
        "required_disclosure": "This is an affiliate link. Results not guaranteed.",
    },
    "US": {
        "banned": {"adult_content": ["adult", "porn"]},  # Less strict than UAE
        "high_risk": {"financial_services": ["loan", "credit"]},
        "safe": UAE_SAFE_CATEGORIES,
        "required_disclosure": "Affiliate disclosure: This post contains affiliate links.",
    },
    "EU": {
        "banned": UAE_BANNED_CATEGORIES,  # Similar to UAE
        "high_risk": UAE_HIGH_RISK_CATEGORIES,
        "safe": UAE_SAFE_CATEGORIES,
        "required_disclosure": "This content contains affiliate links. EU disclosure required.",
    },
    "GLOBAL": {
        "banned": {"adult_content": ["adult", "porn"]},  # Lowest common denominator
        "high_risk": {},
        "safe": UAE_SAFE_CATEGORIES,
        "required_disclosure": "Affiliate link disclosure.",
    },
}


def check_compliance_violations(
    title: str, description: str, category: str | None = None, geo_target: str = "AE"
) -> Dict[str, Any]:
    """
    Check if content violates geo-specific compliance rules.
    Returns compliance assessment with risk flags.
    """
    geo_rules = GEO_COMPLIANCE_RULES.get(geo_target.upper(), GEO_COMPLIANCE_RULES["AE"])
    
    # Normalize text for analysis
    text = f"{title} {description} {category or ''}".lower()
    
    violations = []
    risk_flags = []
    compliance_score = 100
    
    # Check banned categories (hard block)
    for banned_cat, keywords in geo_rules["banned"].items():
        for keyword in keywords:
            if keyword in text:
                violations.append({
                    "category": "banned",
                    "type": banned_cat,
                    "keyword": keyword,
                    "severity": "hard_block",
                })
                compliance_score = 0
                risk_flags.append(f"UAE_BANNED_{banned_cat.upper()}")
    
    # Check high-risk categories
    for risk_cat, keywords in geo_rules["high_risk"].items():
        for keyword in keywords:
            if keyword in text:
                violations.append({
                    "category": "high_risk",
                    "type": risk_cat,
                    "keyword": keyword,
                    "severity": "manual_review",
                })
                compliance_score -= 30
                risk_flags.append(f"UAE_HIGH_RISK_{risk_cat.upper()}")
    
    # Check for risky language patterns
    risky_patterns = [
        (r"\bguaranteed?\b", "guarantee_claim"),
        (r"\bovernight\s+(success|rich)\b", "get_rich_quick"),
        (r"\b(miracle|magic|instant)\b", "miracle_claim"),
        (r"\b(\d{1,3})%\s+(return|profit|gain)\b", "income_claim"),
        (r"\b(lose|gain)\s+(\d+)\s+(kg|lbs|pounds)\b", "medical_claim"),
    ]
    
    for pattern, flag in risky_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            violations.append({
                "category": "language",
                "type": flag,
                "pattern": pattern,
                "severity": "manual_review",
            })
            compliance_score -= 10
            risk_flags.append(f"LANGUAGE_{flag.upper()}")
    
    # Ensure compliance score doesn't go negative
    compliance_score = max(0, compliance_score)
    
    # Determine auto-block status
    auto_block = any(v["severity"] == "hard_block" for v in violations)
    
    return {
        "geo_target": geo_target,
        "compliance_score": compliance_score,
        "auto_block": auto_block,
        "violations": violations,
        "risk_flags": risk_flags,
        "required_disclosure": geo_rules["required_disclosure"],
        "status": "blocked" if auto_block else ("pending_manual_review" if compliance_score < 80 else "compliant"),
    }


def is_uae_compliant(opportunity: Dict[str, Any]) -> bool:
    """
    Quick check if opportunity passes UAE compliance.
    Returns False for any banned categories.
    """
    title = opportunity.get("title", "")
    description = opportunity.get("description", "")
    category = opportunity.get("category", "")
    
    compliance = check_compliance_violations(title, description, category, "AE")
    return not compliance["auto_block"]


def generate_compliance_prompt(base_prompt: str, geo_target: str = "AE") -> str:
    """
    Add compliance instructions to LLM prompts.
    """
    geo_rules = GEO_COMPLIANCE_RULES.get(geo_target.upper(), GEO_COMPLIANCE_RULES["AE"])
    
    banned_list = ", ".join([f"{k} ({', '.join(v[:3])})" for k, v in geo_rules["banned"].items()])
    high_risk_list = ", ".join([f"{k} ({', '.join(v[:3])})" for k, v in geo_rules["high_risk"].items()])
    
    compliance_prompt = f"""
    
COMPLIANCE REQUIREMENTS FOR {geo_target}:
- ABSOLUTELY AVOID: {banned_list}
- HIGH RISK (requires careful handling): {high_risk_list}
- SAFE CATEGORIES: {', '.join(geo_rules['safe'].keys())}
- REQUIRED DISCLOSURE: {geo_rules['required_disclosure']}

CONTENT RULES:
1. Do not make guaranteed income claims
2. Avoid "overnight success" language
3. No medical efficacy claims
4. Include required disclosure naturally
5. Focus on product features, not results

{base_prompt}
"""
    return compliance_prompt


def filter_opportunities_by_compliance(
    opportunities: List[Dict[str, Any]], geo_target: str = "AE"
) -> Dict[str, Any]:
    """
    Filter opportunities based on compliance.
    Returns compliant, high_risk, and blocked lists.
    """
    compliant = []
    high_risk = []
    blocked = []
    
    for opp in opportunities:
        title = opp.get("title", "")
        description = opp.get("description", "")
        category = opp.get("category", "")
        
        compliance = check_compliance_violations(title, description, category, geo_target)
        
        # Add compliance data to opportunity
        opp["compliance"] = compliance
        
        if compliance["auto_block"]:
            blocked.append(opp)
        elif compliance["compliance_score"] < 80:
            high_risk.append(opp)
        else:
            compliant.append(opp)
    
    return {
        "compliant": compliant,
        "high_risk": high_risk,
        "blocked": blocked,
        "geo_target": geo_target,
        "total_processed": len(opportunities),
    }
