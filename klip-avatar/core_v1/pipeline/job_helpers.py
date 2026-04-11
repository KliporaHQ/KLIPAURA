"""Shared path + job helpers for Core V1 pipeline."""

from __future__ import annotations

import sys
from pathlib import Path

# Execution root is ``core_v1`` only (do not prepend KLIP-AVATAR repo root).
_CORE_ROOT = Path(__file__).resolve().parents[1]
if str(_CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CORE_ROOT))


def klip_root() -> Path:
    """Core V1 root (directory containing ``pipeline``, ``engine``, ``services``)."""
    return _CORE_ROOT
