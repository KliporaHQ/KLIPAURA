from utils.logger import log_stage, log_error, log_structured
from utils.state import state
from pipeline.stages.script_stage import run as script_run
from pipeline.stages.media_stage import run as media_run
from pipeline.stages.render_stage import run as render_run
from pipeline.stages.publish_stage import run as publish_run
from config import USE_REDIS
from services.redis_bridge import RedisBridge
import time
import threading
from datetime import datetime
from typing import Any, Dict, Optional


def _default_job(topic: str = "") -> Dict[str, Any]:
    return {
        "topic": topic,
        "avatar_id": "default",
        "product_url": None,
        "affiliate_data": None,
        "layout_mode": None,
        "video_config": {"aspect_ratio": "9:16", "duration_tier": "SHORT"},
        "mode": "TOPIC",
    }


def run_pipeline(job: Optional[Dict[str, Any]] = None, topic: Optional[str] = None):
    """Run the full production pipeline with error handling."""
    if job is None:
        job = _default_job(topic or "")
    else:
        job = dict(job)
        if topic and not job.get("topic"):
            job["topic"] = topic
        job.setdefault("avatar_id", "default")
        job.setdefault("video_config", {"aspect_ratio": "9:16", "duration_tier": "SHORT"})
        job.setdefault("mode", "TOPIC")

    label = (job.get("topic") or job.get("product_url") or "job")[:120]
    log_structured("orchestrator", f"Starting production pipeline for: {label}", "info")

    if USE_REDIS:
        bridge = RedisBridge()
        if bridge.enabled:
            bridge.push_job(job)
            log_structured("orchestrator", f"Job pushed to Redis for {label}, exiting direct pipeline", "info")
            return

    state.reset()
    state.update(
        stage="script",
        progress=0,
        status="running",
        current_topic=label,
        pipeline_mode=job.get("mode"),
        video_config=job.get("video_config"),
        started_at=datetime.now().isoformat(),
    )

    stages = [
        ("SCRIPT", script_run, 10, 30),
        ("MEDIA", media_run, 35, 60),
        ("RENDER", render_run, 65, 95),
        ("PUBLISH", publish_run, 95, 100),
    ]

    results = {}

    try:
        for stage_name, stage_func, start_prog, end_prog in stages:
            log_stage(stage_name, f"Starting {stage_name.lower()} stage", start_prog)
            try:
                if stage_name == "SCRIPT":
                    result = stage_func(job)
                    results["script"] = result
                elif stage_name == "MEDIA":
                    result = stage_func(results.get("script", ""), job)
                    results["assets"] = result
                elif stage_name == "RENDER":
                    result = stage_func(results.get("assets", {}))
                    results["video_path"] = result
                else:
                    result = stage_func(results.get("video_path", ""))
                log_stage(stage_name, f"{stage_name} stage completed", end_prog)
            except Exception as e:
                log_error(stage_name.lower(), str(e))
                state.update(error=str(e))
                raise

        state.update(
            stage="completed",
            progress=100,
            status="completed",
            output=results.get("video_path", ""),
        )
        log_structured(
            "orchestrator",
            "Pipeline completed successfully",
            "info",
            video_path=results.get("video_path"),
        )
        return {"success": True, "video_path": results.get("video_path"), "topic": label, "results": results}

    except Exception as e:
        log_error("orchestrator", f"Pipeline failed: {str(e)}")
        state.update(stage="error", status="failed", progress=0, error=str(e))
        return {"success": False, "error": str(e)}


def run_pipeline_thread(job: Dict[str, Any]):
    """Run in thread for non-blocking."""
    thread = threading.Thread(target=run_pipeline, kwargs={"job": job})
    thread.daemon = True
    thread.start()
    return thread
