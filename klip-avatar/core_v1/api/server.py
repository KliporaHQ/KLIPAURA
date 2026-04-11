from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator
from typing import Dict, Any, Optional
import os
import sys

# Core V1 is the only execution root (engine.*, services.*, pipeline.* live under core_v1).
_CORE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _CORE_ROOT)

import path_bootstrap  # noqa: F401  # klipaura_core on path for WaveSpeed / Redis helpers

from pipeline.orchestrator import run_pipeline_thread
from utils.state import state
from services.storage import ensure_output_dir, get_output_path
from config import USE_REDIS, BASE_DIR, OUTPUT_DIR, validate_required_environment

from engine.cinematic_v2.video_config import validate_aspect_ratio, validate_duration_tier

from api.ugc_routes import router as ugc_router


app = FastAPI(title="KLIP-AVATAR Core V1 - Mission Control")
app.include_router(ugc_router)


@app.on_event("startup")
async def _startup_core_v1():
    """Ensure env and output dir when app is started without main.py (e.g. uvicorn api.server:app)."""
    validate_required_environment()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve frontend static files - use BASE_DIR for standalone deployment
frontend_dir = str(BASE_DIR / "frontend")
app.mount("/static", StaticFiles(directory=frontend_dir), name="static")


class VideoConfig(BaseModel):
    aspect_ratio: str = Field(default="9:16", description='One of "9:16" | "1:1" | "16:9"')
    duration_tier: str = Field(default="SHORT", description='One of SHORT | STANDARD | LONG')

    @model_validator(mode="after")
    def _validate_vc(self) -> "VideoConfig":
        self.aspect_ratio = validate_aspect_ratio(self.aspect_ratio)
        self.duration_tier = validate_duration_tier(self.duration_tier)
        return self


class GenerateRequest(BaseModel):
    topic: Optional[str] = None
    product_url: Optional[str] = None
    avatar_id: str = "default"
    video_config: VideoConfig = Field(default_factory=VideoConfig)

    @model_validator(mode="after")
    def _topic_or_product(self) -> "GenerateRequest":
        has_topic = bool((self.topic or "").strip())
        has_url = bool((self.product_url or "").strip())
        if not has_topic and not has_url:
            raise ValueError("Either topic or product_url is required")
        return self


def _job_from_request(req: GenerateRequest) -> Dict[str, Any]:
    product_url = (req.product_url or "").strip()
    mode = "AFFILIATE" if product_url else "TOPIC"
    return {
        "topic": (req.topic or "").strip(),
        "product_url": product_url or None,
        "avatar_id": (req.avatar_id or "default").strip() or "default",
        "video_config": req.video_config.model_dump(),
        "mode": mode,
    }


@app.post("/pipeline/run")
async def run_pipeline_endpoint(request: GenerateRequest):
    """Start the pipeline (non-blocking thread)."""
    ensure_output_dir()
    job = _job_from_request(request)
    run_pipeline_thread(job)
    label = job.get("topic") or job.get("product_url") or "job"
    return JSONResponse(
        content={
            "status": "started",
            "topic": label,
            "mode": job["mode"],
            "video_config": job["video_config"],
            "message": "Pipeline running in background",
        }
    )


@app.get("/pipeline/status")
async def get_status():
    """Get current pipeline status."""
    current_state = state.get()
    current_state["mode"] = "REDIS" if USE_REDIS else "DIRECT"
    return JSONResponse(content=current_state)


@app.get("/pipeline/logs")
async def get_logs():
    """Get logs."""
    current_state = state.get()
    return JSONResponse(content={"logs": current_state.get("logs", []), "count": len(current_state.get("logs", []))})


@app.get("/pipeline/output")
async def get_output():
    """Get output video path or file."""
    current_state = state.get()
    video_path = current_state.get("output") or get_output_path()
    if os.path.exists(video_path):
        return FileResponse(video_path, media_type="video/mp4", filename="generated_video.mp4")
    return JSONResponse(content={"video_path": video_path, "exists": False, "message": "Video not yet generated"})


@app.get("/health")
async def health():
    """Health check endpoint for production monitoring."""
    return {"status": "ok", "service": "klip-avatar-core-v1", "version": "1.5"}


@app.get("/")
async def root():
    """Serve the dashboard UI."""
    try:
        html_path = BASE_DIR / "frontend" / "index.html"
        with open(html_path, "r", encoding="utf-8") as f:
            html_content = f.read()
        return HTMLResponse(content=html_content)
    except Exception as e:
        return {
            "message": "KLIP-AVATAR Core V1 Dashboard API",
            "endpoints": {
                "run": "POST /pipeline/run",
                "status": "GET /pipeline/status",
                "logs": "GET /pipeline/logs",
                "output": "GET /pipeline/output",
                "health": "GET /health",
                "ugc_generate": "POST /api/ugc/generate",
                "ugc_status": "GET /api/ugc/status/{job_id}",
            },
            "status": "running",
            "ui_error": str(e),
        }


if __name__ == "__main__":
    import uvicorn

    ensure_output_dir()
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
