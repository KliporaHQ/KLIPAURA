"""
Influencer Engine — InfluencerPipeline (orchestration only).

Wires existing avatar, script, voice, video, and publish modules.
No new logic; builds context and delegates to pipeline.run().
"""

from __future__ import annotations

from typing import Any, Dict


class InfluencerPipeline:
    """
    Runnable pipeline: get/create avatar → script → voice → video → publish.
    Uses existing modules in this service (avatar, script_agent, voice_renderer, video_renderer, publish).
    """

    def run(self, job_id: str, config: dict) -> Dict[str, Any]:
        """
        Run the full influencer pipeline for one job.

        Args:
            job_id: Job identifier.
            config: At least topic, niche; optional avatar_profile, hook, blueprint, execution_mode, etc.

        Returns:
            Result dict with video_asset, metrics, stages_completed, etc.
        """
        # Build context in the shape expected by pipeline.run (payload.config + job_id)
        payload = {"job_id": job_id, "config": dict(config)}
        context = {
            "job_id": job_id,
            "service_id": "influencer_engine",
            "payload": payload,
        }
        # Delegate to existing pipeline (script → voice → video → thumbnail → optional publish)
        try:
            from services.influencer_engine.pipeline import run as pipeline_run
        except ImportError:
            from ..pipeline import run as pipeline_run
        return pipeline_run(context)
