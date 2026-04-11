"""
KLIP-CORE Queue Names
======================
Centralized queue and key names for all modules.
Ensures consistency across the entire system.
"""

from dataclasses import dataclass
from typing import Dict


@dataclass
class QueueNames:
    """
    All Redis queue and key names in one place.
    Use QUEUE_NAMES.xxx everywhere in code.
    """
    
    # ─── Jobs Queue ────────────────────────────────────────────────────────
    jobs_pending: str = "klipaura:jobs:pending"
    jobs_processing: str = "klipaura:jobs:processing"
    jobs_completed: str = "klipaura:jobs:completed"
    dlq: str = "klipaura:jobs:dlq"  # Dead Letter Queue
    
    # ─── HITL Queue ─────────────────────────────────────────────────────────
    hitl_pending: str = "klipaura:hitl:pending"
    hitl_approved: str = "klipaura:hitl:approved"
    hitl_rejected: str = "klipaura:hitl:rejected"
    
    # ─── Selector Queue ─────────────────────────────────────────────────────
    selector_opportunities: str = "klipaura:selector:opportunities"
    selector_queue: str = "klipaura:selector:queue"
    selector_blacklist: str = "klipaura:selector:blacklist"
    
    # ─── Funnel Queue ──────────────────────────────────────────────────────
    funnel_projects: str = "klipaura:funnel:projects"
    funnel_active: str = "klipaura:funnel:active"
    funnel_archived: str = "klipaura:funnel:archived"
    
    # ─── Aventure Queue ────────────────────────────────────────────────────
    aventure_pain_points: str = "klipaura:aventure:pain_points"
    aventure_mvps: str = "klipaura:aventure:mvps"
    aventure_testing: str = "klipaura:aventure:testing"
    aventure_scaled: str = "klipaura:aventure:scaled"
    aventure_killed: str = "klipaura:aventure:killed"
    
    # ─── Kill Switches ─────────────────────────────────────────────────────
    kill_global: str = "klipaura:kill:global"
    kill_scanner: str = "klipaura:kill:scanner"
    kill_selector: str = "klipaura:kill:selector"
    kill_avatar: str = "klipaura:kill:avatar"
    kill_funnel: str = "klipaura:kill:funnel"
    kill_aventure: str = "klipaura:kill:aventure"
    kill_trader: str = "klipaura:kill:trader"
    
    # ─── Event Stream ──────────────────────────────────────────────────────
    events_stream: str = "klipaura:events:stream"
    events_log: str = "klipaura:events:log"

    # ─── Global queue pause (workers must check this key) ─────────────────
    global_queue_paused: str = "klipaura:queue:paused"
    
    # ─── Worker Heartbeats ──────────────────────────────────────────────────
    worker_heartbeat: str = "klipaura:worker:heartbeat"
    worker_scanner: str = "klipaura:worker:scanner"
    worker_selector: str = "klipaura:worker:selector"
    worker_avatar: str = "klipaura:worker:avatar"
    worker_funnel: str = "klipaura:worker:funnel"
    worker_aventure: str = "klipaura:worker:aventure"
    
    # ─── Rate Limiting ─────────────────────────────────────────────────────
    rate_daily_videos: str = "klipaura:rate:daily_videos"
    rate_daily_funnels: str = "klipaura:rate:daily_funnels"
    rate_daily_mvps: str = "klipaura:rate:daily_mvps"
    
    # ─── Market Sentiment (Trader → Selector) ─────────────────────────────
    market_sentiment: str = "klipaura:market:sentiment"

    # ─── Scanner trigger (Mission Control scheduler → scanner worker) ───────
    scanner_run_requested: str = "klipaura:scanner:run_requested"

    # ─── Spawn gate (revenue milestone → operator / next avatar prep) ─────
    spawn_ready: str = "klipaura:spawn:ready"
    
    # ─── Budget Tracking ───────────────────────────────────────────────────
    budget_daily_videos: str = "klipaura:budget:daily_videos"
    budget_daily_cost: str = "klipaura:budget:daily_cost"
    
    # ─── Session/Jobs Data ──────────────────────────────────────────────────
    manifest_prefix: str = "klipaura:manifest:"
    
    def get_manifest_key(self, job_id: str) -> str:
        """Get the manifest key for a job."""
        return f"{self.manifest_prefix}{job_id}"
    
    def get_blacklist_key(self, url_hash: str) -> str:
        """Get the blacklist key for a URL hash."""
        return f"{self.selector_blacklist}:{url_hash}"
    
    def get_kill_key(self, module: str) -> str:
        """Get the kill switch key for a module."""
        return f"klipaura:kill:{module}"
    
    def get_worker_key(self, module: str) -> str:
        """Get the worker heartbeat key for a module."""
        return f"klipaura:worker:{module}"


# Singleton instance
QUEUE_NAMES = QueueNames()


# ─── Event Channels (for Pub/Sub) ────────────────────────────────────────────

EVENT_CHANNELS = {
    "all": "klipaura:events:all",
    "scanner": "klipaura:events:scanner",
    "selector": "klipaura:events:selector",
    "avatar": "klipaura:events:avatar",
    "funnel": "klipaura:events:funnel",
    "aventure": "klipaura:events:aventure",
    "trader": "klipaura:events:trader",
    "system": "klipaura:events:system",
}


# ─── Module Names ─────────────────────────────────────────────────────────────

MODULE_NAMES = {
    "scanner": "klip-scanner",
    "selector": "klip-selector",
    "avatar": "klip-avatar",
    "funnel": "klip-funnel",
    "aventure": "klip-aventure",
    "trader": "klip-trader",
    "mc_api": "mission-control-api",
    "mc_ui": "mission-control-ui",
}


# ─── Job Status ────────────────────────────────────────────────────────────────

class JobStatus:
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"
    DEAD_LETTER = "dead_letter"


# ─── Avatar Job Status ────────────────────────────────────────────────────────

class AvatarJobStatus:
    PENDING = "pending"
    SCRIPTTING = "scriptting"
    GENERATING = "generating"
    RENDERING = "rendering"
    UPLOADING = "uploading"
    HITL_PENDING = "hitl_pending"
    APPROVED = "approved"
    POSTING = "posting"
    COMPLETED = "completed"
    FAILED = "failed"


# ─── Funnel Status ─────────────────────────────────────────────────────────────

class FunnelStatus:
    DRAFT = "draft"
    BUILDING = "building"
    TESTING = "testing"
    DEPLOYED = "deployed"
    ARCHIVED = "archived"
    FAILED = "failed"


# ─── MVP Status ───────────────────────────────────────────────────────────────

class MVPStatus:
    IDEA = "idea"
    BUILDING = "building"
    TESTING = "testing"
    SCALING = "scaling"
    KILLED = "killed"
    BRANCHED = "branched"
