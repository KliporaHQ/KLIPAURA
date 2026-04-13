"""Redis list key names — canonical klipaura: prefix, shared by worker, hitl_server, and klip-core."""

JOBS_PENDING = "klipaura:jobs:pending"
JOBS_PAUSED = "klipaura:jobs:paused"
HITL_PENDING = "klipaura:hitl:pending"
DLQ = "klipaura:jobs:dlq"

QUEUE_GLOBAL_PAUSED_KEY = "klipaura:queue:paused"
BLACKLIST_PREFIX = "klipaura:blacklist:"

# Worker heartbeat — hitl_server reads this key to determine worker liveness.
WORKER_AVATAR_HEARTBEAT_KEY = "klipaura:worker:avatar"
WORKER_HEARTBEAT_TTL_SECONDS = 120
WORKER_HEARTBEAT_INTERVAL_SECONDS = 10

# Per-worker registry (SADD on start, SREM on shutdown)
WORKERS_REGISTRY_KEY = "klipaura:workers:registry"

# Product passports (bare key — Redis client adds klipaura: namespace)
PASSPORT_PREFIX = "product:passport:"

# Market sentiment (published by selector, read by scorer)
MARKET_SENTIMENT_KEY = "klipaura:market:sentiment"

# Credits / budget tracking
CREDITS_DAILY_SPEND_KEY = "klipaura:credits:daily_spend"
