"""
Influencer Engine — Avatar Visual Generator.

Generates avatar portrait image from visual_profile.
Uses image generation API from .env when available; otherwise writes a local placeholder PNG
so the dashboard can show an image and you can test the full flow without an API key.
"""

from __future__ import annotations

import os
import zlib
import struct
from typing import Any, Dict, Optional

def _ensure_env_loaded() -> None:
    """Load .env from KLIPAURA_ROOT (or legacy KLIPORA_ROOT) or cwd so WAVESPEED_API_KEY etc. are available."""
    try:
        from dotenv import load_dotenv
        root = (os.environ.get("KLIPAURA_ROOT") or os.environ.get("KLIPORA_ROOT") or "").strip()
        if root:
            load_dotenv(os.path.join(root, ".env"), override=True)
        load_dotenv(override=True)
    except Exception:
        pass


try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


def _write_placeholder_png(output_path: str, seed_id: str) -> bool:
    """
    Write a 256x256 placeholder PNG (gradient + "Avatar" style) so the dashboard
    can show something and the generate-face flow is testable without an API key.
    Returns True if written.
    """
    try:
        w, h = 256, 256
        # Deterministic gradient from seed
        seed = hash(seed_id) % (2 ** 32)
        r0, g0, b0 = 0xE8, 0xC4, 0xB8  # soft skin tone
        r1, g1, b1 = 0x8B, 0x69, 0x6B  # earth / pink
        raw = bytearray()
        for y in range(h):
            raw.extend(b"\x00")  # filter byte (None)
            for x in range(w):
                t = (x + y + (seed % 100)) / (w + h + 100)
                t = max(0, min(1, t))
                raw.extend(bytes([
                    int(r0 + (r1 - r0) * t),
                    int(g0 + (g1 - g0) * t),
                    int(b0 + (b1 - b0) * t),
                ]))
        # PNG compress
        def png_chunk(chunk_type: bytes, data: bytes) -> bytes:
            chunk = chunk_type + data
            return struct.pack(">I", len(data)) + chunk + struct.pack(">I", zlib.crc32(chunk) & 0xFFFFFFFF)
        compressed = zlib.compress(bytes(raw), 9)
        signature = b"\x89PNG\r\n\x1a\n"
        ihdr = struct.pack(">2I5B", w, h, 8, 2, 0, 0, 0)  # 8-bit RGB (color type 2)
        with open(output_path, "wb") as f:
            f.write(signature)
            f.write(png_chunk(b"IHDR", ihdr))
            f.write(png_chunk(b"IDAT", compressed))
            f.write(png_chunk(b"IEND", b""))
        return True
    except Exception:
        return False


def _build_image_prompt(
    visual_profile: Dict[str, Any],
    description: Optional[str] = None,
) -> str:
    """
    Build a consistent image generation prompt from visual_profile.
    When description is provided, use it as the primary prompt so the avatar matches the exact description.
    """
    if description and (description := description.strip()):
        # Use the exact description as the main prompt for faithful avatar generation
        desc_clean = description.replace("\n", " ").strip()[:800]
        parts = [
            f"Professional portrait photograph: {desc_clean}",
            "Soft lighting, cinematic, head and shoulders, facing camera",
            "Highly realistic, 4K, studio quality, neutral background",
        ]
        return " ".join(parts)
    if not visual_profile:
        return "Professional portrait, soft lighting, 4K, studio quality"
    age = (visual_profile.get("age_range") or "25").split("-")[0].strip()
    gender = (visual_profile.get("gender") or "person").lower()
    ethnicity = (visual_profile.get("ethnicity") or "diverse").replace("_", " ")
    skin = (visual_profile.get("skin_tone") or "medium").replace("_", " ")
    features = (visual_profile.get("face_features") or "friendly, professional")
    attire = (visual_profile.get("attire") or "smart casual")
    parts = [
        f"A {age}-year-old {ethnicity} {gender}",
        f"with {skin} complexion",
        f"{features} facial features",
        f"homely and warm expression" if "homely" in features.lower() else "warm expression",
        f"wearing {attire}",
        "long dark hair" if gender == "female" else "",
        "soft natural makeup" if gender == "female" else "",
        "soft lighting, cinematic portrait",
        "highly realistic, 4K, studio quality",
    ]
    return ", ".join(p for p in parts if p).strip()


def generate_avatar_image(
    visual_profile: Dict[str, Any],
    output_path: Optional[str] = None,
    style_consistency_id: Optional[str] = None,
    description: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Generate avatar portrait image from visual_profile.
    If description is provided, the prompt is built from it for exact match to the written avatar description.

    Uses OPENAI_API_KEY (DALL-E), WAVESPEED_API_KEY (Flux), IMAGE_GEN_API_URL, or Pollinations.
    Otherwise returns placeholder.

    Returns:
        dict with url, path (if file written), prompt_used, mock (bool).
    """
    _ensure_env_loaded()
    prompt = _build_image_prompt(visual_profile, description=description)
    seed_id = style_consistency_id or (visual_profile.get("style_consistency_id") if isinstance(visual_profile, dict) else None)

    # 1) OpenAI DALL-E (if OPENAI_API_KEY set)
    api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if api_key and output_path:
        ok, result = _generate_via_openai(prompt, api_key, output_path)
        if ok and result:
            return {
                "url": result.get("url") or f"file://{output_path}",
                "path": result.get("path") or output_path,
                "prompt_used": prompt,
                "style_consistency_id": seed_id,
                "mock": False,
            }

    # 2) Wavespeed (Flux) — same key as video/TTS; image, video, TTS in one platform
    ws_key = (os.environ.get("WAVESPEED_API_KEY") or "").strip()
    if ws_key and output_path:
        ok, result = _generate_via_wavespeed(prompt, ws_key, output_path)
        if ok and result:
            return {
                "url": result.get("url") or f"file://{output_path}",
                "path": result.get("path") or output_path,
                "prompt_used": prompt,
                "style_consistency_id": seed_id,
                "mock": False,
            }

    # 3) Generic IMAGE_GEN_API_URL (POST { "prompt": "..." } -> { "url": "..." } or binary)
    image_gen_url = (os.environ.get("IMAGE_GEN_API_URL") or "").strip()
    if image_gen_url and output_path:
        ok, result = _generate_via_url(prompt, image_gen_url, output_path)
        if ok and result:
            return {
                "url": result.get("url") or f"file://{output_path}",
                "path": result.get("path") or output_path,
                "prompt_used": prompt,
                "style_consistency_id": seed_id,
                "mock": False,
            }

    # 4) Pollinations.ai — free, no API key required (optional POLLINATIONS_API_KEY for higher limits)
    if output_path and (os.environ.get("AVATAR_IMAGE_USE_POLLINATIONS", "true").strip().lower() in ("1", "true", "yes")):
        ok, result = _generate_via_pollinations(prompt, output_path)
        if ok and result:
            return {
                "url": result.get("url") or f"file://{output_path}",
                "path": result.get("path") or output_path,
                "prompt_used": prompt,
                "style_consistency_id": seed_id,
                "mock": False,
            }

    # 5) No API key — write a local placeholder PNG so dashboard can show it and you can test the flow
    placeholder_id = (seed_id or "avatar_placeholder").replace(" ", "_")
    path = None
    if output_path:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        if _write_placeholder_png(output_path, placeholder_id):
            path = output_path
    return {
        "url": f"file://{path}" if path else f"placeholder://avatar/{placeholder_id}",
        "path": path,
        "prompt_used": prompt,
        "style_consistency_id": seed_id,
        "mock": True,
    }


def _generate_via_openai(prompt: str, api_key: str, output_path: str) -> tuple[bool, Optional[Dict[str, Any]]]:
    """Call OpenAI Images API (DALL-E 2/3); save to output_path. Returns (ok, result_dict)."""
    try:
        import json
        import urllib.request
        import urllib.error
        import base64
        url = "https://api.openai.com/v1/images/generations"
        data = json.dumps({
            "model": os.environ.get("OPENAI_IMAGE_MODEL") or "dall-e-2",
            "prompt": prompt[:1000],
            "n": 1,
            "size": "1024x1024",
        }).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            out = json.loads(resp.read().decode())
        b64 = (out.get("data") or [{}])[0].get("b64_json")
        if b64 and output_path:
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            with open(output_path, "wb") as f:
                f.write(base64.b64decode(b64))
            return True, {"path": output_path, "url": f"file://{output_path}"}
        url_asset = (out.get("data") or [{}])[0].get("url")
        if url_asset:
            return True, {"url": url_asset, "path": output_path}
    except Exception as e:
        err_msg = str(e)
        if hasattr(e, "read") and callable(getattr(e, "read", None)):
            try:
                body = e.read()
                if body and hasattr(body, "decode"):
                    err_msg = body.decode("utf-8", errors="replace")[:500]
            except Exception:
                pass
        print(f"  OpenAI image API error: {err_msg[:500]}")
    return False, None


def _generate_via_wavespeed(prompt: str, api_key: str, output_path: str) -> tuple[bool, Optional[Dict[str, Any]]]:
    """Generate image via Wavespeed (Flux). Submit task, poll until completed, download image. Same key as video/TTS."""
    try:
        import json
        import time
        import urllib.request
        base = "https://api.wavespeed.ai/api/v3"
        model = (os.environ.get("WAVESPEED_IMAGE_MODEL") or "wavespeed-ai/flux-dev").strip()
        if not model.startswith("wavespeed-ai/"):
            model = f"wavespeed-ai/{model}" if "/" not in model else model
        url = f"{base}/{model}"
        body = {
            "prompt": prompt[:1000],
            "size": "1024*1024",
            "num_inference_steps": 28,
            "guidance_scale": 3.5,
            "num_images": 1,
            "seed": -1,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            out = json.loads(resp.read().decode())
        if out.get("code") != 200:
            print(f"  Wavespeed image API error: {out.get('message', out)[:300]}")
            return False, None
        data = out.get("data") or {}
        task_id = data.get("id")
        get_url = (data.get("urls") or {}).get("get") or f"{base}/predictions/{task_id}"
        if not task_id:
            print("  Wavespeed image API error: no task id in response")
            return False, None
        # Poll until completed (max 120s)
        for _ in range(40):
            time.sleep(3)
            req = urllib.request.Request(get_url, headers={"Authorization": f"Bearer {api_key}"}, method="GET")
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read().decode())
            if result.get("code") != 200:
                continue
            rdata = result.get("data") or {}
            status = (rdata.get("status") or "").lower()
            if status in ("completed", "succeeded", "complete", "success"):
                outputs = rdata.get("outputs") or []
                if not outputs:
                    print("  Wavespeed image API error: completed but no outputs")
                    return False, None
                img_url = outputs[0] if isinstance(outputs[0], str) else (outputs[0].get("url") or outputs[0])
                with urllib.request.urlopen(urllib.request.Request(img_url, method="GET"), timeout=30) as img_resp:
                    img_data = img_resp.read()
                if img_data and len(img_data) > 100:
                    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
                    with open(output_path, "wb") as f:
                        f.write(img_data)
                    return True, {"path": output_path, "url": f"file://{output_path}"}
                break
            if status == "failed":
                print(f"  Wavespeed image API error: task failed — {str(rdata)[:200]}")
                return False, None
        else:
            print("  Wavespeed image API error: timed out waiting for result")
    except Exception as e:
        print(f"  Wavespeed image API error: {str(e)[:300]}")
    return False, None


def _generate_via_url(prompt: str, base_url: str, output_path: Optional[str]) -> tuple[bool, Optional[Dict[str, Any]]]:
    """POST prompt to IMAGE_GEN_API_URL; expect { url } or binary. Returns (ok, result_dict)."""
    try:
        import json
        import urllib.request
        import urllib.error
        data = json.dumps({"prompt": prompt[:2000]}).encode("utf-8")
        req = urllib.request.Request(
            base_url.rstrip("/"),
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read()
        try:
            out = json.loads(body.decode())
            if isinstance(out, dict) and out.get("url"):
                return True, {"url": out["url"], "path": output_path}
        except Exception:
            pass
        if body and output_path:
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            with open(output_path, "wb") as f:
                f.write(body)
            return True, {"path": output_path, "url": f"file://{output_path}"}
    except Exception:
        pass
    return False, None


def _generate_via_pollinations(prompt: str, output_path: str) -> tuple[bool, Optional[Dict[str, Any]]]:
    """Generate image via Pollinations.ai (free, no key required). GET gen.pollinations.ai/image/{prompt}."""
    try:
        import urllib.request
        from urllib.parse import quote
        prompt_short = prompt[:500].strip()  # URL length limit
        encoded = quote(prompt_short, safe="")
        url = f"https://gen.pollinations.ai/image/{encoded}"
        key = (os.environ.get("POLLINATIONS_API_KEY") or "").strip()
        if key:
            url = f"{url}?key={quote(key, safe='')}"
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; rv:109.0) Gecko/20100101 Firefox/115.0")
        with urllib.request.urlopen(req, timeout=90) as resp:
            body = resp.read()
        if body and len(body) > 100:
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            with open(output_path, "wb") as f:
                f.write(body)
            return True, {"path": output_path, "url": f"file://{output_path}"}
    except Exception as e:
        print(f"  Pollinations image API error: {str(e)[:300]}")
    return False, None
