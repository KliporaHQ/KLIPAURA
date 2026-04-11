"""
Generate avatar images from config descriptions.

Loads avatar_profiles.yaml, generates the avatar face for each profile using the
exact description (and visual_profile), and caches to data/avatar_assets/{id}/face.png.

Requires OPENAI_API_KEY or IMAGE_GEN_API_URL in .env for real generation; otherwise
writes a placeholder and logs the prompt that would be used.

Run from repo root (KLIPORA MASTER AUTOMATION):
  cd E:\\KLIPORA\\KLIPORA MASTER AUTOMATION
  python -m services.influencer_engine.scripts.generate_avatar_images

Or with PYTHONPATH set from E:\\KLIPORA:
  set PYTHONPATH=E:\\KLIPORA\\KLIPORA MASTER AUTOMATION
  python KLIPORA MASTER AUTOMATION/services/influencer_engine/scripts/generate_avatar_images.py
"""

from __future__ import annotations

import os
import sys

# Paths: script -> scripts/ -> influencer_engine/ -> services/ -> KLIPORA MASTER AUTOMATION
_script_dir = os.path.dirname(os.path.abspath(__file__))
_engine_dir = os.path.dirname(_script_dir)
_services_dir = os.path.dirname(_engine_dir)
_repo_root = os.path.dirname(_services_dir)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)
os.chdir(_repo_root)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_repo_root, ".env"))
    # Also load parent repo .env (e.g. E:\KLIPORA\.env) so OPENAI_API_KEY can live there
    _parent = os.path.dirname(_repo_root)
    if _parent != _repo_root:
        load_dotenv(os.path.join(_parent, ".env"))
except Exception:
    pass


def main() -> None:
    import yaml
    from services.influencer_engine.avatar.avatar_assets import generate_avatar_face, get_avatar_assets_dir

    config_path = os.path.join(_engine_dir, "config", "avatar_profiles.yaml")
    if not os.path.isfile(config_path):
        print(f"Config not found: {config_path}")
        return

    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    avatars = data.get("avatars") or {}
    openai_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    ws_key = (os.environ.get("WAVESPEED_API_KEY") or "").strip()
    if openai_key:
        print("Image generation: will try OpenAI (DALL-E), then Wavespeed (Flux) if needed.")
    elif ws_key:
        print("Image generation: using WAVESPEED_API_KEY (Flux) — same key as video/TTS.")
    else:
        print("Set OPENAI_API_KEY or WAVESPEED_API_KEY in .env for real avatar images.")

    if not avatars:
        print("No avatars in config.")
        return

    api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    image_url = (os.environ.get("IMAGE_GEN_API_URL") or "").strip()
    if not api_key and not image_url:
        print("Set OPENAI_API_KEY or IMAGE_GEN_API_URL in .env for real image generation.")
        print("Running anyway: prompt will be logged and placeholder may be used.\n")

    for avatar_id, profile in avatars.items():
        profile = dict(profile)
        profile["avatar_id"] = profile.get("id") or avatar_id
        profile["id"] = profile["avatar_id"]
        print(f"Generating avatar: {avatar_id} ...")
        result = generate_avatar_face(profile)
        if result.get("error"):
            print(f"  Error: {result['error']}")
            continue
        if result.get("mock"):
            print(f"  Mock/placeholder (no API key or API failed). Prompt was built from description.")
        else:
            path = result.get("path")
            print(f"  Saved: {path}")
        out_dir = get_avatar_assets_dir(profile["avatar_id"])
        face_path = os.path.join(out_dir, "face.png")
        if os.path.isfile(face_path):
            print(f"  Cached at: {face_path}")
    print("Done.")


if __name__ == "__main__":
    main()
