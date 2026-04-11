"""
Create a sample ~30s video using .env API keys (Groq, ElevenLabs, ffmpeg).

Loads KLIPORA MASTER AUTOMATION/.env, picks a trending topic for the avatar,
runs the pipeline in production mode, and prints the video path.

Usage (from KLIPORA MASTER AUTOMATION):
  set PYTHONPATH=E:\KLIPORA\KLIPORA MASTER AUTOMATION
  python -m services.influencer_engine.scripts.create_sample_video [avatar_id]
Default avatar_id: nova
"""

from __future__ import annotations

import os
import sys
import uuid

# Paths
_script_dir = os.path.dirname(os.path.abspath(__file__))
_engine_dir = os.path.dirname(_script_dir)
_services_dir = os.path.dirname(_engine_dir)
_repo_root = os.path.dirname(_services_dir)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)
os.chdir(_repo_root)

# Load .env first so pipeline/agents see GROQ_API_KEY, ELEVENLABS_API_KEY, etc.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_repo_root, ".env"))
except Exception:
    pass


def main() -> None:
    avatar_id = (sys.argv[1] if len(sys.argv) > 1 else "nova").strip()
    from services.influencer_engine.scheduler.influencer_scheduler import get_avatar_profile
    from services.influencer_engine.agents.trend_agent import TrendAgent
    from services.influencer_engine.pipeline import run as pipeline_run

    profile = get_avatar_profile(avatar_id)
    if not profile:
        print(f"Avatar not found: {avatar_id}")
        return
    niche = (profile.get("niche") or "general").strip() or "general"
    trends = TrendAgent().discover_trends_for_niche(niche)
    topic = (trends[0].get("topic") or f"Trending in {niche}") if trends else f"Trending in {niche}"
    job_id = f"sample_{avatar_id}_{uuid.uuid4().hex[:8]}"
    config = {
        "avatar_profile": avatar_id,
        "topic": topic,
        "hook": "",
        "execution_mode": "production",
        "publish": False,
    }
    context = {"payload": {"job_id": job_id, "config": config}}
    print(f"Creating sample video: avatar={avatar_id}, topic={topic}, mode=production")
    result = pipeline_run(context)
    if not result.get("ok"):
        print("Error:", result.get("content_error", "pipeline_failed"))
        return
    print("Stages:", result.get("stages_completed", []))
    va = result.get("video_asset") or {}
    path = va.get("path") or (va.get("url") or "").replace("file://", "")
    if path and os.path.isfile(path) and os.path.getsize(path) > 0:
        print(f"Video saved: {path}")
    else:
        if not path or not os.path.isfile(path):
            print("Video not saved. Install ffmpeg and add it to PATH for local .mp4 output.")
        else:
            print("Video file was empty (ffmpeg may have failed). Check that ffmpeg is on PATH.")
        print("Video asset:", va)
    if result.get("cost_summary"):
        print("Cost:", result["cost_summary"])


if __name__ == "__main__":
    main()
