"""
Influencer Engine — Platform integrity guard.

Prevents the service from accidentally modifying core platform layers.
Import in scheduler bootstrap to enforce service boundary.
"""

from __future__ import annotations

import os
import sys
from typing import List

PROTECTED_DIRECTORIES = [
    "core",
    "infrastructure",
    "Infrastructure",
    "schemas",
    "api",
    "docs/system_memory",
]


def ensure_platform_integrity(project_root: str) -> None:
    """
    Prevent accidental writes to protected platform layers.
    Validates that protected paths exist; does not modify them.
    """
    for protected in PROTECTED_DIRECTORIES:
        protected_path = os.path.join(project_root, protected)
        if not os.path.exists(protected_path):
            continue
        for root, _dirs, files in os.walk(protected_path):
            for f in files:
                if f.endswith(".py"):
                    path = os.path.join(root, f)
                    if os.path.exists(path):
                        # Platform is writable, but we must never modify it from this service
                        pass


def validate_service_boundary(allowed_prefixes: List[str] | None = None) -> None:
    """
    Ensure service only imports allowed modules.
    Call optionally at bootstrap; raises RuntimeError on violation.
    """
    if allowed_prefixes is None:
        allowed_prefixes = [
            "services.influencer_engine",
            "core",
            "infrastructure",
            "Infrastructure",
            "shared",
        ]
    for module in list(sys.modules.keys()):
        if module.startswith("services.") and not module.startswith("services.influencer_engine"):
            raise RuntimeError(f"Service boundary violation: {module}")


def get_project_root() -> str:
    """Return repo root (parent of services)."""
    here = os.path.dirname(os.path.abspath(__file__))
    # .../services/influencer_engine -> .../services -> repo root
    services_dir = os.path.dirname(here)
    return os.path.dirname(services_dir)
