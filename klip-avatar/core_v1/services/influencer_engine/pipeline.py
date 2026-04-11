"""
Influencer Engine — pipeline entrypoint.

Runs one video job; optionally runs analyze_performance when distribution_result is present.
execution_mode: "mock" (default) | "production" — controls real vs mock APIs.
Full flow: generate_script → generate_voice → compose_video → thumbnail → save assets → optional publish.
"""

from __future__ import annotations

import os
import shutil
import sys
from typing import Any, Dict

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


def _service_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def _ensure_path():
    _dir = _service_dir()
    repo = os.path.dirname(os.path.dirname(_dir))
    for p in (repo, _dir):
        if p not in sys.path:
            sys.path.insert(0, p)


def _execution_mode(context: dict) -> str:
    """execution_mode = 'mock' | 'production'. Default: mock (safe)."""
    payload = context.get("payload") or context
    config = payload.get("config") or {}
    mode = (config.get("execution_mode") or os.environ.get("INFLUENCER_ENGINE_MODE") or "mock").strip().lower()
    return mode if mode in ("mock", "production") else "mock"


def _emit(event_type: str, payload: Dict[str, Any]) -> None:
    """Emit event to platform (Redis + optional EventBus)."""
    try:
        from core.service_manager.utils.service_utils import event_publish
        event_publish(event_type, payload)
    except Exception:
        pass
    try:
        from core.service_manager.utils.event_bus_publisher import get_publisher
        pub = get_publisher()
        if pub is not None:
            pub.publish(event_type, payload, source="influencer_engine")
    except Exception:
        pass


def _load_distribution_and_profiles(avatar_profile_name: str, topic: str):
    """Load avatar profiles (manual + store) and resolve platform via DistributionAgent (path-safe for worker load)."""
    _ensure_path()
    try:
        from scheduler.influencer_scheduler import get_avatar_profile
    except Exception:
        from ..scheduler.influencer_scheduler import get_avatar_profile
    try:
        from klipaura_core.agents.distribution_agent import DistributionAgent
    except ImportError:
        return {}
    profile = get_avatar_profile(avatar_profile_name) or {}
    return DistributionAgent.optimize_platform_target(topic, profile)


def analyze_performance(context: dict) -> dict | None:
    """
    Final stage: use AnalyticsAgent to compute performance_metrics and performance_score;
    emit CONTENT_PERFORMANCE; update strategy memory and emit STRATEGY_UPDATE;
    if experiment_id+variant present, record result and emit EXPERIMENT_RESULT when both A and B done.
    """
    _ensure_path()
    payload = context.get("payload") or context
    config = payload.get("config") or {}
    video_asset = (payload.get("video_asset") or context.get("video_asset") or {})
    distribution_result = (payload.get("distribution_result") or context.get("distribution_result") or {})
    avatar_id = (config.get("avatar_profile") or "")
    try:
        try:
            from scheduler.influencer_scheduler import get_avatar_profile
        except Exception:
            from ..scheduler.influencer_scheduler import get_avatar_profile
        profile = get_avatar_profile(avatar_id) or {}
        niche = (profile.get("niche") or "") if isinstance(profile, dict) else (config.get("niche") or "")
    except Exception:
        niche = (config.get("niche") or "")
    topic = (config.get("topic") or "")
    hook = (config.get("hook") or "")
    platform = (config.get("blueprint") or {}).get("platform_target") or ""
    experiment_id = config.get("experiment_id")
    variant = config.get("variant")

    try:
        from klipaura_core.agents.analytics_agent import AnalyticsAgent
        from learning.strategy_memory import update_from_performance, record_experiment_result, get_strategy
    except Exception:
        from ..agents.analytics_agent import AnalyticsAgent
        from ..learning.strategy_memory import update_from_performance, record_experiment_result, get_strategy

    agent = AnalyticsAgent()
    out = agent.collect_performance(video_asset, distribution_result, {"avatar": avatar_id, "niche": niche})
    metrics = out.get("performance_metrics") or {}
    score = out.get("performance_score") or 0.0

    _emit("CONTENT_PERFORMANCE", {
        "service_id": "influencer_engine",
        "avatar": avatar_id,
        "topic": topic,
        "performance_metrics": metrics,
        "performance_score": score,
        "experiment_id": experiment_id,
        "variant": variant,
    })

    update_from_performance(avatar_id, niche, topic, hook, platform, score)
    try:
        from avatar.avatar_memory_store import update_avatar_learning
        update_avatar_learning(avatar_id, {"topic": topic, "hook": hook, "platform": platform, "score": score, "metrics": metrics})
    except Exception:
        try:
            from ..avatar.avatar_memory_store import update_avatar_learning
            update_avatar_learning(avatar_id, {"topic": topic, "hook": hook, "platform": platform, "score": score, "metrics": metrics})
        except Exception:
            pass
    strategy = get_strategy(avatar_id)
    _emit("STRATEGY_UPDATE", {
        "service_id": "influencer_engine",
        "avatar": avatar_id,
        "niche": strategy.get("niche"),
        "best_hooks": strategy.get("best_hooks", [])[:5],
        "best_topics": strategy.get("best_topics", [])[:5],
        "best_platform": strategy.get("best_platform"),
    })

    if experiment_id and variant:
        exp_result = record_experiment_result(experiment_id, variant, score)
        if exp_result:
            _emit("EXPERIMENT_RESULT", {
                "service_id": "influencer_engine",
                "experiment_id": experiment_id,
                "winning_variant": exp_result.get("winning_variant"),
                "score": exp_result.get("score"),
                "score_a": exp_result.get("score_a"),
                "score_b": exp_result.get("score_b"),
            })

    return out


def _run_content_generation(context: dict, execution_mode: str) -> dict:
    """
    Run full content pipeline: script → voice → video → thumbnail → save assets.
    Returns result with script_asset, audio_asset, video_asset, thumbnail_asset and cost_summary.
    """
    _ensure_path()
    payload = context.get("payload") or context
    config = payload.get("config") or {}
    job_id = (payload.get("job_id") or "").strip() or "ie_job"
    avatar_id = config.get("avatar_profile") or ""
    topic = config.get("topic") or "trending_topic"
    hook = config.get("hook") or ""
    try:
        from scheduler.influencer_scheduler import get_avatar_profile
    except Exception:
        from ..scheduler.influencer_scheduler import get_avatar_profile
    profile = get_avatar_profile(avatar_id) or {}
    persona = (config.get("persona") or (profile.get("tone") if profile else "") or "").strip() or "engaging creator"
    use_real_apis = execution_mode == "production"

    result = {"ok": True, "stages": [], "script_asset": None, "audio_asset": None, "video_asset": None, "thumbnail_asset": None}
    progress_callback = context.get("progress_callback")

    # Strict state machine: Script -> Voice -> Video -> Thumbnail (no skip, no reorder)
    try:
        from pipeline_state import PipelineStateMachine, PipelineStage
    except Exception:
        from .pipeline_state import PipelineStateMachine, PipelineStage
    state_machine = PipelineStateMachine(job_id)

    def _progress(stage: str, data: dict = None):
        if progress_callback:
            try:
                progress_callback(stage, data or {})
            except Exception:
                pass

    try:
        from klipaura_core.agents.script_agent import generate_script_with_llm
        from rendering.voice_renderer import generate_voice
        from rendering.video_renderer import VideoRenderer
        from rendering.thumbnail_renderer import ThumbnailRenderer
        from assets.asset_store import save_script_json, save_audio_file, save_video_file, save_thumbnail_file, save_asset
        from assets.asset_pipeline import register_assets_from_pipeline
        from cost.cost_tracker import record_llm_cost, record_tts_cost, get_cost_for_job
        from cost.pricing import compute_llm_cost, compute_tts_cost
    except Exception:
        from .agents.script_agent import generate_script_with_llm
        from .rendering.voice_renderer import generate_voice
        from .rendering.video_renderer import VideoRenderer
        from .rendering.thumbnail_renderer import ThumbnailRenderer
        from .assets.asset_store import save_script_json, save_audio_file, save_video_file, save_thumbnail_file, save_asset
        from .assets.asset_pipeline import register_assets_from_pipeline
        from .cost.cost_tracker import record_llm_cost, record_tts_cost, get_cost_for_job
        from .cost.pricing import compute_llm_cost, compute_tts_cost

    # 1) Script (LLM in production, mock fallback; inject avatar tone + signature phrase)
    if not state_machine.is_allowed(PipelineStage.SCRIPT):
        raise ValueError(f"Pipeline state violation: next allowed stage is {state_machine.next_stage()}, not script.")
    _progress("generate_script", {"status": "started"})
    override = (config.get("narration_override") or "").strip()
    if override:
        try:
            from shared.uae_content_safety import enforce_safety_on_content

            ok_uae, uae_msg = enforce_safety_on_content(topic, override)
            if not ok_uae:
                raise ValueError(uae_msg or "SAFETY BLOCK: Content blocked by UAE compliance screening.")
        except ValueError:
            raise
        except Exception:
            pass
        script_data = {
            "hook": override[:400],
            "main_content": "",
            "cta": "",
            "hashtags": "#PalmJumeirah #Dubai #LuxuryRealEstate #AriaVeda",
            "compliance_pass": True,
            "compliance_reason": "Director-approved narration override; non-investment disclaimer included.",
            "narration": override,
            "full_text": override,
            "mock": False,
            "skip_llm_cost": True,
        }
    else:
        script_data = generate_script_with_llm(topic, persona, hook, avatar_profile=profile)
    if script_data.get("compliance_blocked") or script_data.get("compliance_pass") is False:
        jid = (payload.get("job_id") or job_id or "").strip()
        reason = (script_data.get("compliance_reason") or script_data.get("full_text") or "compliance_pass false").strip()
        try:
            from shared.compliance_abort import cleanup_job_temp_assets, log_compliance_abort

            log_compliance_abort(jid or "unknown", reason, source="influencer_pipeline")
            cleanup_job_temp_assets(jid or "", payload if isinstance(payload, dict) else None)
        except Exception:
            pass
        raise ValueError(reason or "COMPLIANCE ABORT: Legal gate failed.")
    if script_data.get("uae_blocked"):
        msg = (script_data.get("narration") or script_data.get("full_text") or "").strip()
        raise ValueError(msg or "SAFETY BLOCK: Content blocked by UAE compliance screening.")
    narration = script_data.get("narration") or (script_data.get("hook", "") + " " + script_data.get("main_content", "") + " " + script_data.get("cta", ""))
    if use_real_apis and not script_data.get("mock") and not script_data.get("skip_llm_cost"):
        in_tok = script_data.get("usage", {}).get("input_tokens") or script_data.get("input_tokens") or 500
        out_tok = script_data.get("usage", {}).get("output_tokens") or script_data.get("output_tokens") or 300
        record_llm_cost(job_id, compute_llm_cost(in_tok, out_tok), in_tok, out_tok, avatar_id=avatar_id)
    script_asset = save_script_json(script_data, owner_avatar=avatar_id, pipeline_source="influencer_engine")
    result["script_asset"] = script_asset
    result["stages"].append("generate_script")
    state_machine.complete_stage(PipelineStage.SCRIPT, {"script_asset": script_asset})
    _progress("generate_script", {"status": "completed", "asset_id": script_asset.get("asset_id")})

    # 2) Voice (TTS in production, mock fallback; use avatar voice_profile for consistency)
    if not state_machine.is_allowed(PipelineStage.VOICE):
        raise ValueError(f"Pipeline state violation: next allowed stage is {state_machine.next_stage()}, not voice.")
    _progress("generate_voice", {"status": "started"})
    voice_config = config.get("voice") or {"voice_id": "default", "job_id": job_id}
    voice_config = {**voice_config, "avatar_profile": profile}
    voice_out = generate_voice(narration, voice_config, output_path=None)
    if voice_out.get("path"):
        audio_asset = save_audio_file(voice_out["path"], owner_avatar=avatar_id, metadata=voice_out)
    else:
        audio_asset = save_asset("audio", url=voice_out.get("url", ""), owner_avatar=avatar_id, pipeline_source="influencer_engine", metadata=voice_out)
    if use_real_apis and not voice_out.get("mock"):
        chars = len(narration)
        record_tts_cost(job_id, compute_tts_cost(chars), chars, avatar_id=avatar_id)
    result["audio_asset"] = audio_asset
    result["stages"].append("generate_voice")
    state_machine.complete_stage(PipelineStage.VOICE, {"audio_asset": audio_asset, "voice_out": voice_out})
    _progress("generate_voice", {"status": "completed", "asset_id": audio_asset.get("asset_id")})

    # 3) Video — agent plans B-roll from content length and TTS duration, then render
    content_duration = voice_out.get("duration_seconds")
    if not content_duration and voice_out.get("path") and os.path.isfile(voice_out["path"]):
        try:
            import subprocess
            exe = "ffprobe"
            try:
                from .rendering.ffmpeg_path import get_ffmpeg_exe
                base = os.path.dirname(get_ffmpeg_exe())
                exe = os.path.join(base, "ffprobe.exe" if os.name == "nt" else "ffprobe")
                if not os.path.isfile(exe):
                    exe = "ffprobe"
            except Exception:
                pass
            r = subprocess.run(
                [exe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", voice_out["path"]],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0 and r.stdout.strip():
                content_duration = float(r.stdout.strip())
        except Exception:
            pass
    if not content_duration or content_duration <= 0:
        content_duration = max(15.0, min(90.0, len(narration.split()) / 2.5))
    video_plan = None
    if use_real_apis:
        try:
            from klipaura_core.agents.video_planning_agent import plan_video
        except Exception:
            from .agents.video_planning_agent import plan_video
        try:
            video_plan = plan_video(narration, content_duration_sec=content_duration, use_llm_motion_prompts=True)
        except Exception:
            video_plan = None
    video_renderer = VideoRenderer()
    _cached_url = None
    try:
        from avatar.avatar_assets import get_avatar_image_url
        _cached_url = get_avatar_image_url(avatar_id)
    except Exception:
        try:
            from ..avatar.avatar_assets import get_avatar_image_url
            _cached_url = get_avatar_image_url(avatar_id)
        except Exception:
            pass
    avatar_url = _cached_url or (profile.get("brand_style") or {}).get("avatar_image_url") or profile.get("avatar_image_url") or config.get("avatar_url")
    brand_style = profile.get("brand_style") or {}
    _arole = ""
    if isinstance(profile, dict):
        _arole = str(profile.get("avatar_role") or "").strip().lower()
    video_config = {
        "job_id": job_id,
        "audio_url": voice_out.get("url"),
        "audio_path": voice_out.get("path"),
        "duration_seconds": content_duration,
        "script": narration[:500],
        "narration_full": narration,
        "avatar_id": avatar_id,
        "avatar_url": avatar_url,
        "avatar_profile": profile,
        "avatar_role": _arole or "influencer",
        "subtitle_style": brand_style.get("subtitle_style") or "bold centered",
        "background_style": brand_style.get("background_style") or "minimal aesthetic",
        "color_palette": brand_style.get("color_palette"),
        "video_plan": video_plan,
        "seo_title": (profile.get("seo_title") or "") if isinstance(profile, dict) else "",
        "seo_description": (profile.get("seo_description") or "") if isinstance(profile, dict) else "",
        "seo_hashtags": (profile.get("seo_hashtags") or "") if isinstance(profile, dict) else "",
        "social_handle": (profile.get("social_handle") or "") if isinstance(profile, dict) else "",
    }
    if not state_machine.is_allowed(PipelineStage.VIDEO):
        raise ValueError(f"Pipeline state violation: next allowed stage is {state_machine.next_stage()}, not video.")
    _progress("compose_video", {"status": "started"})
    video_out = video_renderer.render(video_config, output_path=None)
    if video_out.get("path") and os.path.isfile(video_out["path"]):
        video_asset = save_video_file(video_out["path"], owner_avatar=avatar_id, metadata=video_out)
        video_asset["mock"] = False
        try:
            from ..avatar.avatar_assets import cache_avatar_assets, get_avatar_assets_dir
        except Exception:
            from services.influencer_engine.avatar.avatar_assets import cache_avatar_assets, get_avatar_assets_dir
        try:
            av_dir = get_avatar_assets_dir(avatar_id)
            dest = os.path.join(av_dir, f"sample_{job_id}.mp4")
            shutil.copy2(video_out["path"], dest)
            latest = os.path.join(av_dir, "latest_sample.mp4")
            shutil.copy2(video_out["path"], latest)
            cache_avatar_assets(avatar_id, sample_video_path=latest)
        except Exception:
            pass
    else:
        video_asset = save_asset("video", url=video_out.get("url", ""), owner_avatar=avatar_id, pipeline_source="influencer_engine", metadata={**(video_out or {}), "mock": True})
        video_asset["mock"] = True
    result["video_asset"] = video_asset
    result["stages"].append("compose_video")
    state_machine.complete_stage(PipelineStage.VIDEO, {"video_asset": video_asset})
    _progress("compose_video", {"status": "completed", "asset_id": video_asset.get("asset_id")})

    # 4) Thumbnail
    if not state_machine.is_allowed(PipelineStage.THUMBNAIL):
        raise ValueError(f"Pipeline state violation: next allowed stage is {state_machine.next_stage()}, not thumbnail.")
    _progress("thumbnail", {"status": "started"})
    thumb_renderer = ThumbnailRenderer()
    thumb_out = thumb_renderer.render({"title": topic, "topic": topic, "avatar_id": avatar_id}, output_path=None)
    if thumb_out.get("path") and os.path.isfile(thumb_out["path"]):
        thumb_asset = save_thumbnail_file(thumb_out["path"], owner_avatar=avatar_id, metadata=thumb_out)
    else:
        thumb_asset = save_asset("thumbnail", url=thumb_out.get("url", ""), owner_avatar=avatar_id, pipeline_source="influencer_engine", metadata=thumb_out)
    result["thumbnail_asset"] = thumb_asset
    result["stages"].append("thumbnail")
    state_machine.complete_stage(PipelineStage.THUMBNAIL, {"thumbnail_asset": thumb_asset})
    _progress("thumbnail", {"status": "completed", "asset_id": thumb_asset.get("asset_id")})

    # Attach to payload for asset_pipeline and distribution
    payload["script_asset"] = result["script_asset"]
    payload["audio_asset"] = result["audio_asset"]
    payload["video_asset"] = result["video_asset"]
    payload["thumbnail_asset"] = result["thumbnail_asset"]
    context["payload"] = payload
    register_assets_from_pipeline(context)
    result["cost_summary"] = get_cost_for_job(job_id)
    return result


def run(context: dict) -> dict:
    """
    Run pipeline for one job. Context contains payload with config (avatar_profile, topic, blueprint).
    execution_mode: "mock" (default) | "production" — use real APIs when production.
    If payload contains distribution_result, runs analyze_performance only.
    Otherwise runs full content generation (script → voice → video → thumbnail → assets) then optional publish.
    """
    payload = context.get("payload") or context
    config = payload.get("config") or {}
    blueprint = config.get("blueprint") or {}
    avatar_profile_name = config.get("avatar_profile") or ""
    topic = config.get("topic") or "auto_discovered"
    execution_mode = _execution_mode(context)

    platform_target = blueprint.get("platform_target")
    if not platform_target and avatar_profile_name:
        try:
            platform_target = _load_distribution_and_profiles(avatar_profile_name, topic)
        except Exception:
            platform_target = "youtube_shorts"

    if not platform_target:
        platform_target = "youtube_shorts"

    result = {
        "ok": True,
        "service": "influencer_engine",
        "execution_mode": execution_mode,
        "context_keys": list(context.keys()),
        "platform_target": platform_target,
        "avatar_profile": avatar_profile_name,
        "topic": topic,
    }

    # When distribution_result is present, run analyze_performance only
    if payload.get("distribution_result") is not None or context.get("distribution_result") is not None:
        try:
            analysis = analyze_performance(context)
            if analysis:
                result["performance_metrics"] = analysis.get("performance_metrics")
                result["performance_score"] = analysis.get("performance_score")
        except Exception:
            pass
        return result

    # Full content generation pipeline (script → voice → video → thumbnail → assets)
    try:
        content_result = _run_content_generation(context, execution_mode)
        result["stages_completed"] = content_result.get("stages", [])
        result["script_asset"] = content_result.get("script_asset")
        result["audio_asset"] = content_result.get("audio_asset")
        result["video_asset"] = content_result.get("video_asset")
        result["thumbnail_asset"] = content_result.get("thumbnail_asset")
        result["cost_summary"] = content_result.get("cost_summary")
        try:
            from publishing.publish_controller import should_publish, get_safety_limits
        except Exception:
            from .publishing.publish_controller import should_publish, get_safety_limits
        limits = get_safety_limits(config)
        publish_config = {**config, "execution_mode": execution_mode, "auto_publish": limits.get("auto_publish", False)}
        try:
            from learning.strategy_memory import get_strategy
            strategy = get_strategy(avatar_profile_name)
        except Exception:
            strategy = {}
        metrics_for_publish = (content_result.get("cost_summary") or {})
        if content_result.get("video_asset") and config.get("publish") and should_publish(publish_config, strategy, metrics_for_publish):
            dist_mode = "real"
            try:
                from distribution.base import publish_video
                video_url = (content_result.get("video_asset") or {}).get("url") or ""
                if video_url.startswith("file://"):
                    video_url = video_url.replace("file://", "")
                title = topic[:100]
                desc = (content_result.get("script_asset") or {}).get("narration", "")[:500]
                hashtags = (content_result.get("script_asset") or {}).get("hashtags", "")
                pub = publish_video(platform_target, video_url, title, desc, {"hashtags": hashtags}, mode=dist_mode)
                result["distribution_result"] = pub
                if pub.get("post_id") and not pub.get("mock"):
                    try:
                        from analytics.post_tracker import register_post
                    except Exception:
                        from .analytics.post_tracker import register_post
                    video_asset = content_result.get("video_asset") or {}
                    register_post(
                        post_id=pub.get("post_id"),
                        platform=platform_target,
                        video_id=video_asset.get("asset_id", ""),
                        topic=topic,
                        hook=(content_result.get("script_asset") or {}).get("hook", ""),
                        avatar_id=avatar_profile_name,
                    )
            except Exception:
                result["distribution_result"] = {"mock": True, "error": "publish_skipped"}
        elif config.get("publish"):
            result["distribution_result"] = {"mock": True, "platform": platform_target}
    except Exception as e:
        result["ok"] = False
        result["content_error"] = str(e)

    return result
