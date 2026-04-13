"""Abstract base class for all affiliate network adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, List


@dataclass
class AdapterProduct:
    """Normalised product row returned by every adapter."""
    network: str              # "amazon" | "temu" | "clickbank" | "manual"
    title: str
    url: str                  # affiliate URL (with tag if available at discovery time)
    source_url: str           # original URL before affiliate conversion
    price: str                # human-readable, e.g. "AED 1,299"
    images: List[str]         # CDN image URLs (may be empty at this stage)
    category: str
    description: str
    commission_rate: float    # e.g. 4.5 for 4.5%
    trend_score: float        # 0.0–1.0 normalised trend signal
    asin: str = ""
    meta: dict = field(default_factory=dict)


class AffiliateAdapter(ABC):
    """Base adapter — each network subclass implements ``fetch()``."""

    network: str = ""

    @abstractmethod
    def fetch(self, limit: int = 20) -> List[AdapterProduct]:
        """Return up to *limit* raw products from the network."""
        ...

    def enrich_images(self, product: AdapterProduct) -> AdapterProduct:
        """Optional hook: subclass may override to resolve image URLs."""
        return product
