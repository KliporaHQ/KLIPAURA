"""
WaveSpeed Flux text-to-image (extracted from Mission Control ``generate_avatar_image_from_prompt``).

Submit → poll → download PNG to a local path. Same API contract and defaults as the original inline code.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import requests


class WaveSpeedT2IError(Exception):
    """User-safe failure from WaveSpeed Flux T2I (message suitable for HTTP detail)."""


def flux_dev_download_to_path(
    prompt: str,
    out_path: str,
    *,
    api_key: str | None = None,
) -> None:
    """
    Generate one square portrait image via WaveSpeed Flux and write bytes to ``out_path``.

    Uses the same model, JSON body, poll loop, and timeouts as ``main.generate_avatar_image_from_prompt``.
    Raises WaveSpeedT2IError on failure.
    """
    ws_key = (api_key or os.getenv("WAVESPEED_API_KEY") or "").strip()
    if not ws_key:
        raise WaveSpeedT2IError("WaveSpeed is not configured for this server")

    p = prompt[:1000].strip()
    if not p:
        raise WaveSpeedT2IError("prompt is required")

    base = "https://api.wavespeed.ai/api/v3"
    model = (os.getenv("WAVESPEED_IMAGE_MODEL") or "wavespeed-ai/flux-dev").strip()
    if not model.startswith("wavespeed-ai/"):
        model = f"wavespeed-ai/{model}" if "/" not in model else model

    try:
        submit = requests.post(
            f"{base}/{model}",
            headers={"Authorization": f"Bearer {ws_key}", "Content-Type": "application/json"},
            json={
                "prompt": p,
                "size": "1024*1024",
                "num_inference_steps": 28,
                "guidance_scale": 3.5,
                "num_images": 1,
                "seed": -1,
            },
            timeout=30,
        )
        submit.raise_for_status()
        out = submit.json()
        if int(out.get("code") or 0) != 200:
            raise WaveSpeedT2IError(str(out.get("message") or "WaveSpeed rejected the image request"))
        data = out.get("data") or {}
        task_id = str(data.get("id") or "").strip()
        get_url = ((data.get("urls") or {}).get("get") or f"{base}/predictions/{task_id}").strip()
        if not task_id:
            raise WaveSpeedT2IError("WaveSpeed did not return a task id")

        img_url = None
        for _ in range(40):
            time.sleep(3)
            poll = requests.get(get_url, headers={"Authorization": f"Bearer {ws_key}"}, timeout=20)
            poll.raise_for_status()
            pdata = poll.json()
            if int(pdata.get("code") or 0) != 200:
                continue
            d2 = pdata.get("data") or {}
            st = str(d2.get("status") or "").lower()
            if st in ("completed", "succeeded", "complete", "success"):
                outs = d2.get("outputs") or []
                if outs:
                    first = outs[0]
                    img_url = first if isinstance(first, str) else (first.get("url") or "")
                break
            if st == "failed":
                raise WaveSpeedT2IError(f"Image generation failed: {str(d2)[:300]}")
        if not img_url:
            raise WaveSpeedT2IError("Image generation timed out; try again later")

        img = requests.get(str(img_url), timeout=30)
        img.raise_for_status()
        if len(img.content or b"") < 100:
            raise WaveSpeedT2IError("The image service returned an invalid image")

        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(img.content)
    except WaveSpeedT2IError:
        raise
    except requests.HTTPError as e:
        raise WaveSpeedT2IError(f"Image service error: {e}") from e
    except Exception as e:
        raise WaveSpeedT2IError(f"Image generation failed: {e}") from e
