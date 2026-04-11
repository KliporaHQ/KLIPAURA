"""
Influencer Engine — Avatar Intelligence.

Avatar generator, store, performance tracking, lifecycle management,
visual identity (image generation), and asset cache.
Manual avatars (avatar_profiles.yaml) remain supported; auto-creation is opt-in.
"""

from .avatar_generator import generate_avatar_profile
from .avatar_store import (
    save_avatar,
    get_avatar,
    list_avatars,
    update_avatar,
    deactivate_avatar,
)
from .avatar_performance import compute_avatar_score
from .avatar_lifecycle import run_lifecycle_tick
from .avatar_visual_generator import generate_avatar_image, _build_image_prompt
from .avatar_assets import (
    cache_avatar_assets,
    get_avatar_assets,
    get_avatar_image_url,
    get_voice_profile_cached,
    get_style_config_cached,
)

__all__ = [
    "generate_avatar_profile",
    "save_avatar",
    "get_avatar",
    "list_avatars",
    "update_avatar",
    "deactivate_avatar",
    "compute_avatar_score",
    "run_lifecycle_tick",
    "generate_avatar_image",
    "_build_image_prompt",
    "cache_avatar_assets",
    "get_avatar_assets",
    "get_avatar_image_url",
    "get_voice_profile_cached",
    "get_style_config_cached",
]
