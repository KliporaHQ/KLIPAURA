"""Strict JSON schema for Groq script outputs (video pipeline contract)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ScriptJsonOutput(BaseModel):
    """Required keys for narration pipeline — rejects malformed LLM JSON early."""

    hook: str = Field(default="", max_length=4000)
    main_content: str = Field(default="", max_length=12000)
    cta: str = Field(default="", max_length=2000)
    hashtags: str = Field(default="", max_length=2000)
    compliance_pass: bool = Field(default=True, description="UAE / platform legal gate from Groq Legal Team")
    compliance_reason: str = Field(default="", max_length=4000)


class NarrationComplianceOutput(BaseModel):
    """Flat narration path (Mission Control / topic_generator) with legal gate."""

    narration_script: str = Field(default="", max_length=32000)
    compliance_pass: bool = Field(default=True)
    compliance_reason: str = Field(default="", max_length=4000)
