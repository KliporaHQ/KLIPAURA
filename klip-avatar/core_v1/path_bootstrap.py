"""
Single execution root: ``core_v1`` only. Adds ``core_v1`` and ``klipaura-core/src`` to ``sys.path``.

Import once at process start (replaces ``klipaura_paths`` for Core V1).
Does not add KLIP-AVATAR repo root.
"""

from __future__ import annotations

import os
import sys

_CORE_V1 = os.path.dirname(os.path.abspath(__file__))
for _p in (
    _CORE_V1,
    os.path.join(_CORE_V1, "scripts"),
    os.path.join(_CORE_V1, "services"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_WORKSPACE = os.path.dirname(_CORE_V1)  # KLIP-AVATAR folder (not inserted)
_KLIPAURA = os.path.dirname(_WORKSPACE)  # monorepo root
_KC_DIR = os.environ.get("KLIPAURA_CORE_PATH") or os.path.join(_KLIPAURA, "klipaura-core")
_KC_SRC = os.path.join(_KC_DIR, "src")
_KC_PATH = _KC_SRC if os.path.isdir(_KC_SRC) else _KC_DIR
if os.path.isdir(_KC_PATH) and _KC_PATH not in sys.path:
    sys.path.append(_KC_PATH)

CORE_V1_ROOT: str = _CORE_V1
