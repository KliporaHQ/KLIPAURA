"""
Influencer Engine — end-to-end execution test.

Run from repo root:
  python services/influencer_engine/run_service_test.py

Or from this directory (with repo root on PYTHONPATH):
  python run_service_test.py

Uses ServiceManager.execute_service to run avatar → script → voice → video → result.
"""

from __future__ import annotations

import os
import sys

# Ensure repo root on path (KLIPORA MASTER AUTOMATION)
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)
os.chdir(_REPO)

from core.service_manager.service_manager import ServiceManager

sm = ServiceManager()

result = sm.execute_service(
    service_id="influencer_engine",
    job_payload={
        "topic": "AI tools that save time",
        "niche": "ai_tools",
        "avatar_profile": "nova",  # use avatar from config/avatar_profiles.yaml
    },
)

print(result)
