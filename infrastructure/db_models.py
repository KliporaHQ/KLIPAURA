from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class OpportunitySource(Base):
    __tablename__ = "opportunity_sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="high")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    last_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Compliance fields
    is_uae_compliant: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    geo_restrictions: Mapped[dict] = mapped_column(JSON, nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    __table_args__ = (
        Index("ix_opportunity_sources_enabled", "enabled"),
        Index("ix_opportunity_sources_priority", "priority"),
    )


class Opportunity(Base):
    __tablename__ = "opportunities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    trend_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    affiliate_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content_angle: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    dedupe_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    
    # Compliance fields
    geo_target: Mapped[str] = mapped_column(String(8), nullable=False, default="AE")
    compliance_score: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    state: Mapped[str | None] = mapped_column(String(32), nullable=True)  # "blocked_compliance", etc.
    compliance_data: Mapped[dict] = mapped_column(JSON, nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    scores: Mapped[list["OpportunityScore"]] = relationship(
        "OpportunityScore",
        back_populates="opportunity",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_opportunities_geo_target", "geo_target"),
        Index("ix_opportunities_compliance_score", "compliance_score"),
        Index("ix_opportunities_state", "state"),
    )


class OpportunityScore(Base):
    __tablename__ = "opportunity_scores"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    opportunity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("opportunities.id", ondelete="CASCADE"), nullable=False)

    momentum_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    audience_fit_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    payout_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content_ease_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    competition_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    risk_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    tier: Mapped[str] = mapped_column(String(1), nullable=False, default="C")
    explain: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    opportunity: Mapped[Opportunity] = relationship(back_populates="scores")

    __table_args__ = (
        Index("ix_opportunity_scores_opportunity_id", "opportunity_id"),
    )


ContentState = Enum(
    "drafting",
    "rendering",
    "pending_review",
    "approved",
    "rejected",
    "scheduled",
    "publishing",
    "published",
    "failed",
    name="content_state",
)


class ContentItem(Base):
    __tablename__ = "content_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("opportunities.id", ondelete="SET NULL"), nullable=True)

    platform_targets: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    state: Mapped[str] = mapped_column(ContentState, nullable=False, default="drafting")

    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    hook: Mapped[str | None] = mapped_column(Text, nullable=True)
    script: Mapped[str | None] = mapped_column(Text, nullable=True)
    cta: Mapped[str | None] = mapped_column(Text, nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    thumbnail_concept: Mapped[str | None] = mapped_column(Text, nullable=True)
    visual_plan: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    affiliate_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    utm_params: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    risk_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    
    # Compliance fields
    geo_target: Mapped[str] = mapped_column(String(8), nullable=False, default="AE")
    compliance_score: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    required_disclosure: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by: Mapped[str] = mapped_column(String(32), nullable=False, default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    assets: Mapped[list["ContentAsset"]] = relationship(
        "ContentAsset",
        back_populates="content",
        cascade="all, delete-orphan",
    )
    approvals: Mapped[list["Approval"]] = relationship(
        "Approval",
        back_populates="content",
        cascade="all, delete-orphan",
    )
    risk_flags: Mapped[list["RiskFlag"]] = relationship(
        "RiskFlag",
        back_populates="content",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_content_items_state", "state"),
        Index("ix_content_items_created_at", "created_at"),
        Index("ix_content_items_geo_target", "geo_target"),
        Index("ix_content_items_compliance_score", "compliance_score"),
    )


class ContentAsset(Base):
    __tablename__ = "content_assets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    content_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("content_items.id", ondelete="CASCADE"), nullable=False)

    asset_type: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    r2_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)

    duration_seconds: Mapped[Numeric | None] = mapped_column(Numeric(10, 3), nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)

    meta: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    content: Mapped[ContentItem] = relationship(back_populates="assets")

    __table_args__ = (
        UniqueConstraint("content_id", "asset_type", "version", name="uq_content_asset_version"),
        Index("ix_content_assets_content_id", "content_id"),
    )


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    content_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("content_items.id", ondelete="CASCADE"), nullable=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False)
    reviewer_user_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_ack: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    content: Mapped[ContentItem] = relationship(back_populates="approvals")

    __table_args__ = (
        Index("ix_approvals_content_id", "content_id"),
    )


class RiskFlag(Base):
    __tablename__ = "risk_flags"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    content_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("content_items.id", ondelete="CASCADE"), nullable=False)

    category: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    flag: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    requires_manual_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    
    # Compliance fields
    geo_target: Mapped[str | None] = mapped_column(String(8), nullable=True)
    auto_block: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    content: Mapped[ContentItem] = relationship(back_populates="risk_flags")

    __table_args__ = (
        Index("ix_risk_flags_content_id", "content_id"),
        Index("ix_risk_flags_requires_manual_review", "requires_manual_review"),
        Index("ix_risk_flags_geo_target", "geo_target"),
        Index("ix_risk_flags_auto_block", "auto_block"),
    )


class WaitlistLead(Base):
    __tablename__ = "waitlist_leads"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="landing_page")
    referred_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    __table_args__ = (
        Index("ix_waitlist_leads_email", "email"),
        Index("ix_waitlist_leads_status", "status"),
        Index("ix_waitlist_leads_created_at", "created_at"),
    )
