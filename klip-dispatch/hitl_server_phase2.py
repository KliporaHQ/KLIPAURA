"""
Phase 2 API endpoints for selector, content generation, and rendering.
Add these endpoints to hitl_server.py to complete Phase 2.
"""

# Add these endpoints to hitl_server.py after the existing endpoints:

@app.get("/api/opportunities/scored")
def get_scored_opportunities(request: Request, tier: str = "A", limit: int = 10) -> dict:
    """Get scored opportunities by tier for content generation."""
    try:
        from klip_scanner.selector_engine import get_top_opportunities
        opportunities = get_top_opportunities(tier, limit)
        return {"ok": True, "opportunities": opportunities}
    except Exception as e:
        raise HTTPException(500, f"Failed to get opportunities: {str(e)}")


@app.post("/api/selector/run")
def run_selector_engine(request: Request, body: SelectorRunBody = SelectorRunBody()) -> dict:
    """Run selector engine to score and classify opportunities."""
    if not (os.getenv("OPS_API_KEY") or "").strip():
        raise HTTPException(503, "OPS_API_KEY not set")
    if not _ops_authorized(request):
        raise HTTPException(401, "Send header X-Ops-Key matching OPS_API_KEY")
    try:
        from klip_scanner.selector_engine import run_selector_engine
        result = run_selector_engine(
            limit=body.limit or 50,
            geo_target="AE"  # UAE default
        )
        return {"ok": True, "result": result}
    except Exception as e:
        raise HTTPException(500, f"Selector engine failed: {str(e)}")


@app.post("/api/content/generate/{opportunity_id}")
def generate_content_for_opportunity(request: Request, opportunity_id: str) -> dict:
    """Generate content for a specific opportunity."""
    try:
        from klip_content.content_generator import generate_content_for_opportunity
        result = generate_content_for_opportunity(opportunity_id, geo_target="AE")
        return {"ok": True, "content": result}
    except Exception as e:
        raise HTTPException(500, f"Content generation failed: {str(e)}")


@app.post("/api/content/batch-generate")
def batch_generate_content(request: Request, body: BatchGenerateBody) -> dict:
    """Generate content for multiple opportunities."""
    try:
        from klip_content.content_generator import batch_generate_content
        result = batch_generate_content(
            body.opportunity_ids,
            geo_target="AE"
        )
        return {"ok": True, "result": result}
    except Exception as e:
        raise HTTPException(500, f"Batch content generation failed: {str(e)}")


@app.get("/api/content")
def get_content_by_state(request: Request, state: str = "rendering", limit: int = 20) -> dict:
    """Get content items by state."""
    try:
        from klip_content.content_generator import get_content_by_state
        items = get_content_by_state(state, limit)
        return {"ok": True, "content": items}
    except Exception as e:
        raise HTTPException(500, f"Failed to get content: {str(e)}")


@app.post("/api/content/{content_id}/render")
def render_content_video(request: Request, content_id: str) -> dict:
    """Render video for content item."""
    try:
        from klip_render.ffmpeg_renderer import create_split_screen_video
        from klip_storage.r2_service import upload_content_asset
        from infrastructure.db import get_session
        from infrastructure.db_models import ContentItem
        
        # Get content details
        with get_session() as sess:
            content = sess.query(ContentItem).filter(
                ContentItem.id == content_id
            ).first()
            if not content:
                raise HTTPException(404, "Content not found")
        
        # For now, use placeholder paths - in production these would come from asset generation
        top_video = "path/to/product/video.mp4"  # Would be generated from product images
        bottom_video = "path/to/avatar/video.mp4"  # Would be lipsync avatar
        
        # Create output path
        output_dir = os.path.join(_JOBS_DIR, content_id)
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, "final_video.mp4")
        
        # Render video
        render_result = create_split_screen_video(
            top_video,
            bottom_video,
            output_path,
            captions_file=None,  # Would be generated from script
            logo_overlay=None,    # Would be avatar logo
            cta_text=content.cta
        )
        
        if not render_result["success"]:
            raise HTTPException(500, f"Render failed: {render_result['error']}")
        
        # Upload to R2
        r2_result = upload_content_asset(
            output_path,
            content_id,
            "final_video",
            metadata=render_result
        )
        
        if not r2_result["success"]:
            raise HTTPException(500, f"R2 upload failed: {r2_result['error']}")
        
        # Update content state
        from klip_content.content_generator import update_content_state
        update_content_state(content_id, "pending_review")
        
        return {
            "ok": True,
            "render_result": render_result,
            "r2_result": r2_result,
            "content_id": content_id,
            "state": "pending_review"
        }
        
    except Exception as e:
        raise HTTPException(500, f"Content render failed: {str(e)}")


@app.get("/api/content/{content_id}/assets")
def get_content_assets(request: Request, content_id: str) -> dict:
    """Get all assets for a content item."""
    try:
        from klip_storage.r2_service import get_r2_service
        service = get_r2_service()
        assets = service.list_assets(content_id)
        return {"ok": True, "assets": assets}
    except Exception as e:
        raise HTTPException(500, f"Failed to get assets: {str(e)}")


@app.get("/api/content/{content_id}/asset/{asset_id}/url")
def get_asset_url(request: Request, content_id: str, asset_id: str) -> dict:
    """Get presigned URL for asset."""
    try:
        from klip_storage.r2_service import get_asset_presigned_url
        url = get_asset_presigned_url(asset_id)
        return {"ok": True, "url": url}
    except Exception as e:
        raise HTTPException(500, f"Failed to get asset URL: {str(e)}")


@app.post("/api/r2/test")
def test_r2_connection(request: Request) -> dict:
    """Test R2 connection."""
    if not (os.getenv("OPS_API_KEY") or "").strip():
        raise HTTPException(503, "OPS_API_KEY not set")
    if not _ops_authorized(request):
        raise HTTPException(401, "Send header X-Ops-Key matching OPS_API_KEY")
    try:
        from klip_storage.r2_service import get_r2_service
        service = get_r2_service()
        result = service.test_connection()
        return result
    except Exception as e:
        raise HTTPException(500, f"R2 test failed: {str(e)}")


# Add these Pydantic models to hitl_server.py imports section:

class BatchGenerateBody(BaseModel):
    opportunity_ids: list[str] = Field(..., min_items=1, max_items=10)


class SelectorRunBody(BaseModel):
    limit: int | None = Field(default=None, ge=1, le=100)
