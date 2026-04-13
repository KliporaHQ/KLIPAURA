"""ProductPassport — the central data contract between klip-selector and klip-avatar worker.

Redis key convention
--------------------
Both ``UpstashRedis`` and ``LocalRedis`` prepend ``"klipaura:"`` automatically via their
``_key()`` method.  To avoid double-namespacing (``klipaura:klipaura:...``) all redis calls
here use **bare keys** — i.e. ``"product:passport:{passport_id}"`` — and let the client
add the namespace prefix.

The canonical full Redis key is therefore: ``klipaura:product:passport:{passport_id}``
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Tuple

# Default 7-day TTL for passports stored in Redis.
PASSPORT_TTL_SECONDS: int = 7 * 24 * 3600  # 604 800 s

_VALID_NETWORKS = {"amazon", "temu", "clickbank", "manual"}
_VALID_STATUSES = {"pending", "queued", "approved", "rejected", "processing", "complete", "failed"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _passport_bare_key(passport_id: str) -> str:
    """Bare Redis key (no klipaura: prefix — client adds it)."""
    return f"product:passport:{passport_id}"


@dataclass
class ProductPassport:
    """Fully enriched product payload that flows from selector → queue → worker → publisher."""

    # ── Identity ────────────────────────────────────────────────────────────────
    passport_id: str          # pp-{uuid4()}, auto-generated via new()
    network: str              # "amazon" | "temu" | "clickbank" | "manual"

    # ── Product data ────────────────────────────────────────────────────────────
    title: str
    images: list              # min 3 CDN image URLs (list[str])
    price: str                # human-readable, e.g. "AED 1,299"
    description: str
    affiliate_url: str        # full affiliate link; may be empty until publisher enriches it
    source_url: str           # original product page URL before affiliate conversion
    commission_rate: float    # percentage, e.g. 4.5 for 4.5 %
    category: str             # niche / product category e.g. "beauty", "kitchen"

    # ── Scoring & routing ───────────────────────────────────────────────────────
    score: float              # 0-100 opportunity score
    video_format: str         # "SplitFormat" | "LipsyncFormat" | "FullscreenFormat" | etc.

    # ── Assignment ──────────────────────────────────────────────────────────────
    avatar_id: str            # assigned avatar; empty string = let worker pick default

    # ── Lifecycle ───────────────────────────────────────────────────────────────
    status: str               # "pending" | "queued" | "approved" | "rejected" | …
    created_at: str           # ISO 8601 UTC timestamp

    # ── Optional enrichment (filled later in pipeline) ──────────────────────────
    r2_url: str = ""          # populated after worker upload
    published_at: str = ""    # populated after GetLate/Zernio publish
    job_id: str = ""          # linked job manifest ID

    # ── Extra metadata (arbitrary per-adapter data) ──────────────────────────────
    meta: dict = field(default_factory=dict)

    # ── Class methods ────────────────────────────────────────────────────────────

    @classmethod
    def new(
        cls,
        *,
        network: str,
        title: str,
        images: list,
        price: str,
        description: str,
        affiliate_url: str,
        commission_rate: float,
        score: float,
        avatar_id: str,
        video_format: str,
        category: str,
        source_url: str = "",
        status: str = "pending",
        meta: Optional[dict] = None,
        **kwargs: Any,
    ) -> "ProductPassport":
        """Factory — auto-generates ``passport_id`` and ``created_at``."""
        return cls(
            passport_id=f"pp-{uuid.uuid4()}",
            network=network,
            title=title,
            images=list(images),
            price=price,
            description=description,
            affiliate_url=affiliate_url,
            source_url=source_url,
            commission_rate=float(commission_rate),
            category=category,
            score=float(score),
            video_format=video_format,
            avatar_id=avatar_id,
            status=status,
            created_at=_now_iso(),
            meta=dict(meta or {}),
        )

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: dict) -> "ProductPassport":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        filtered = {k: v for k, v in d.items() if k in known}
        # Coerce numeric fields that might come back as strings from Redis.
        for numeric_key in ("commission_rate", "score"):
            if numeric_key in filtered:
                try:
                    filtered[numeric_key] = float(filtered[numeric_key])
                except (TypeError, ValueError):
                    filtered[numeric_key] = 0.0
        if "images" in filtered and not isinstance(filtered["images"], list):
            try:
                filtered["images"] = json.loads(filtered["images"])
            except Exception:
                filtered["images"] = []
        if "meta" in filtered and not isinstance(filtered["meta"], dict):
            try:
                filtered["meta"] = json.loads(filtered["meta"])
            except Exception:
                filtered["meta"] = {}
        return cls(**filtered)

    @classmethod
    def from_json(cls, raw: str) -> "ProductPassport":
        return cls.from_dict(json.loads(raw))

    # ── Validation ────────────────────────────────────────────────────────────

    def is_valid(self) -> Tuple[bool, str]:
        """Return ``(True, "")`` if passport is ready to queue, else ``(False, reason)``."""
        if len(self.images) < 3:
            return False, "MISSING_MIN_IMAGES"
        if not self.title.strip():
            return False, "MISSING_TITLE"
        if not self.affiliate_url.strip():
            return False, "MISSING_AFFILIATE_URL"
        if self.network not in _VALID_NETWORKS:
            return False, f"INVALID_NETWORK:{self.network}"
        return True, ""

    # ── Redis persistence ─────────────────────────────────────────────────────

    def save(self, redis_client: Any, ttl_seconds: int = PASSPORT_TTL_SECONDS) -> None:
        """Persist to Redis.

        Uses bare key ``"product:passport:{passport_id}"``; the redis client adds the
        ``"klipaura:"`` namespace prefix automatically, yielding the full key
        ``klipaura:product:passport:{passport_id}``.
        """
        key = _passport_bare_key(self.passport_id)
        redis_client.setex(key, self.to_json(), ttl_seconds)

    @classmethod
    def load(
        cls,
        redis_client: Any,
        passport_id: str,
    ) -> Optional["ProductPassport"]:
        """Load from Redis by passport_id.  Returns ``None`` if not found or corrupted."""
        key = _passport_bare_key(passport_id)
        raw = redis_client.get(key)
        if not raw:
            return None
        try:
            return cls.from_json(raw)
        except Exception:
            return None

    def update_status(self, redis_client: Any, status: str, ttl_seconds: int = PASSPORT_TTL_SECONDS) -> None:
        """Convenience: change status field and re-save (full overwrite, resets TTL)."""
        self.status = status
        self.save(redis_client, ttl_seconds)

    # ── Repr ─────────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"<ProductPassport {self.passport_id} network={self.network!r} "
            f"score={self.score:.1f} status={self.status!r} avatar={self.avatar_id!r}>"
        )


__all__ = ["ProductPassport", "PASSPORT_TTL_SECONDS"]
