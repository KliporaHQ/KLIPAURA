"""
KLIP-CORE Event Schemas
=======================
Pydantic models for all events in the system.
"""

from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field
import uuid


class EventSeverity(str, Enum):
    """Event severity levels."""
    DEBUG = "debug"
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class ModuleName(str, Enum):
    """All system modules."""
    SCANNER = "scanner"
    SELECTOR = "selector"
    AVATAR = "avatar"
    FUNNEL = "funnel"
    AVENTURE = "aventure"
    TRADER = "trader"
    SYSTEM = "system"


class EventType(str, Enum):
    """
    All event types in the system.
    Format: {module}_{action}
    """
    # Scanner events
    SCANNER_SCAN_STARTED = "scanner_scan_started"
    SCANNER_SCAN_COMPLETED = "scanner_scan_completed"
    SCANNER_OPPORTUNITY_FOUND = "scanner_opportunity_found"
    
    # Selector events
    SELECTOR_SELECTION_STARTED = "selector_selection_started"
    SELECTOR_JOB_QUEUED = "selector_job_queued"
    SELECTOR_JOB_ROUTED = "selector_job_routed"
    SELECTOR_BLACKLIST_ADDED = "selector_blacklist_added"
    
    # Avatar events
    AVATAR_JOB_STARTED = "avatar_job_started"
    AVATAR_SCRIPT_GENERATED = "avatar_script_generated"
    AVATAR_VIDEO_GENERATED = "avatar_video_generated"
    AVATAR_JOB_COMPLETED = "avatar_job_completed"
    AVATAR_HITL_PENDING = "avatar_hitl_pending"
    AVATAR_VIDEO_APPROVED = "avatar_video_approved"
    AVATAR_VIDEO_REJECTED = "avatar_video_rejected"
    AVATAR_VIDEO_POSTED = "avatar_video_posted"
    AVATAR_JOB_FAILED = "avatar_job_failed"
    
    # Funnel events
    FUNNEL_PAGE_STARTED = "funnel_page_started"
    FUNNEL_PAGE_DEPLOYED = "funnel_page_deployed"
    FUNNEL_VISITOR_RECORDED = "funnel_visitor_recorded"
    FUNNEL_CONVERSION_RECORDED = "funnel_conversion_recorded"
    FUNNEL_PAGE_ARCHIVED = "funnel_page_archived"
    
    # Aventure events
    AVENTURE_MVP_STARTED = "aventure_mvp_started"
    AVENTURE_PAIN_POINT_FOUND = "aventure_pain_point_found"
    AVENTURE_MVP_SCALED = "aventure_mvp_scaled"
    AVENTURE_MVP_KILLED = "aventure_mvp_killed"
    
    # System events
    SYSTEM_KILL_SWITCH_TRIGGERED = "system_kill_switch_triggered"
    SYSTEM_KILL_SWITCH_CLEARED = "system_kill_switch_cleared"
    SYSTEM_MODULE_ONLINE = "system_module_online"
    SYSTEM_MODULE_OFFLINE = "system_module_offline"
    SYSTEM_ERROR = "system_error"


class KlipEvent(BaseModel):
    """
    The canonical event model for all KLIPAURA events.
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    
    # Who generated this event
    module: ModuleName
    
    # What happened
    event_type: str
    
    # How severe
    severity: EventSeverity = EventSeverity.INFO
    
    # Human-readable summary
    message: str
    
    # Structured payload
    data: Dict[str, Any] = Field(default_factory=dict)
    
    # Optional job reference
    job_id: Optional[str] = None
    
    class Config:
        use_enum_values = True


# ─── Event Display Config ──────────────────────────────────────────────────────

EVENT_DISPLAY: Dict[str, Dict[str, str]] = {
    # Scanner
    "scanner_scan_started": {"icon": "🔍", "color": "blue"},
    "scanner_scan_completed": {"icon": "✅", "color": "green"},
    "scanner_opportunity_found": {"icon": "💡", "color": "purple"},
    
    # Selector
    "selector_selection_started": {"icon": "⚙️", "color": "blue"},
    "selector_job_queued": {"icon": "📋", "color": "blue"},
    "selector_job_routed": {"icon": "🛤️", "color": "blue"},
    
    # Avatar
    "avatar_job_started": {"icon": "🎬", "color": "blue"},
    "avatar_script_generated": {"icon": "✍️", "color": "blue"},
    "avatar_video_generated": {"icon": "🎥", "color": "blue"},
    "avatar_job_completed": {"icon": "✅", "color": "green"},
    "avatar_hitl_pending": {"icon": "👀", "color": "yellow"},
    "avatar_video_approved": {"icon": "👍", "color": "green"},
    "avatar_video_rejected": {"icon": "👎", "color": "red"},
    "avatar_video_posted": {"icon": "📢", "color": "green"},
    "avatar_job_failed": {"icon": "❌", "color": "red"},
    
    # Funnel
    "funnel_page_started": {"icon": "🏗️", "color": "blue"},
    "funnel_page_deployed": {"icon": "🚀", "color": "green"},
    "funnel_visitor_recorded": {"icon": "👤", "color": "gray"},
    "funnel_conversion_recorded": {"icon": "💰", "color": "green"},
    "funnel_page_archived": {"icon": "📦", "color": "gray"},
    
    # Aventure
    "aventure_mvp_started": {"icon": "🧪", "color": "blue"},
    "aventure_pain_point_found": {"icon": "🎯", "color": "purple"},
    "aventure_mvp_scaled": {"icon": "📈", "color": "green"},
    "aventure_mvp_killed": {"icon": "💀", "color": "red"},
    
    # System
    "system_kill_switch_triggered": {"icon": "🛑", "color": "red"},
    "system_kill_switch_cleared": {"icon": "▶️", "color": "green"},
    "system_module_online": {"icon": "🟢", "color": "green"},
    "system_module_offline": {"icon": "🔴", "color": "red"},
    "system_error": {"icon": "⚠️", "color": "red"},
}


def get_event_display(event_type: str) -> Dict[str, str]:
    """Get display config for an event type."""
    return EVENT_DISPLAY.get(event_type, {"icon": "📌", "color": "gray"})
