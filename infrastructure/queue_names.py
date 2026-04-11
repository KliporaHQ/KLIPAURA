"""Redis list key names (logical; redis_client prepends klipaura:)."""

JOBS_PENDING = "klip:jobs:pending"
JOBS_PAUSED = "klip:jobs:paused"
HITL_PENDING = "klip:hitl:pending"
DLQ = "klip:dlq"

QUEUE_GLOBAL_PAUSED_KEY = "klip:queue:paused"
BLACKLIST_PREFIX = "klip:blacklist:"
