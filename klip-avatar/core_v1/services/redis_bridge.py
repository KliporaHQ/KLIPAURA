import os
import json
from typing import Any, Dict, Optional
from pathlib import Path
from config import config
from utils.logger import log, log_structured, log_error

class RedisBridge:
    """Optional Redis bridge for Core V1.5 - falls back to direct execution."""
    
    def __init__(self):
        self.enabled = config.USE_REDIS
        self.redis_client = None
        self._init_redis()
    
    def _init_redis(self):
        """Initialize Redis only if enabled."""
        if not self.enabled:
            log("Redis bridge disabled - using direct mode", "REDIS_BRIDGE")
            return
        
        try:
            from klipaura_core.infrastructure.redis_client import RedisClient
            self.redis_client = RedisClient()
            log("Connected to Redis via klipaura-core", "REDIS_BRIDGE")
        except Exception as e:
            try:
                import redis
                redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
                self.redis_client = redis.from_url(redis_url)
                log("Connected to Redis directly", "REDIS_BRIDGE")
            except Exception as redis_err:
                log_error("REDIS_BRIDGE", f"Redis unavailable: {redis_err}. Falling back to direct mode.")
                self.enabled = False
    
    def push_job(self, payload):
        """Push job to Redis — string topic (legacy) or JSON object (full pipeline job)."""
        if not self.enabled:
            return False

        try:
            if isinstance(payload, (dict, list)):
                raw = json.dumps(payload, default=str)
            else:
                raw = str(payload)
            self.redis_client.lpush("klipaura:jobs", raw)
            return True
        except Exception:
            return False
    
    def get_result(self, job_id: str, timeout: int = 30) -> Optional[Dict]:
        """Poll for result from Redis (optional)."""
        if not self.enabled:
            return None
        # Stub for result polling - in full system would use response queue
        log("Result polling stub - direct mode preferred", "REDIS_BRIDGE")
        return None

# Singleton
redis_bridge = RedisBridge()
