#!/usr/bin/env python3
"""
Temu URL → short-form 9:16 MP4 (scraped assets only, hook scenes, MAD logging).

Run from ``core_v1``:
  python -m pipeline.temu_shortform_ugc

Env (optional; does **not** change ``ugc_pipeline.py`` / affiliate defaults):
  ``PRODUCT_URL`` — Temu product page (https)
  ``PRICE_TEXT`` — coupon line (AED parsed for CTA)
  ``AFFILIATE_URL`` — informational only (not burned; optional)
  ``TEMU_SHORTFORM_IMAGE_URLS`` or ``UGC_PRODUCT_IMAGE_URLS`` — comma-separated
  ``https://img.kwcdn.com/...`` URLs when Temu returns a bot shell to ``requests``

Outputs (monorepo root ``KLIPAURA/``):
  ``assets/product/`` — downloads (``gallery_*``, ``hero*``, optional ``product_embedded.mp4``)
  ``output/video.mp4`` — 1080×1920, ~10s, 30fps, burned captions + EQ motion pulse

Logs: ``SPLIT_TOP_RATIO`` (read-only), ``m12``/``m23``, unique asset URL count, asset list.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urljoin, urlunparse

_CORE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore[misc, assignment]

if load_dotenv:
    load_dotenv(
        dotenv_path=os.path.join(_CORE, ".env"),
        override=False,
        encoding="utf-8-sig",
    )

import path_bootstrap  # noqa: F401

import requests
from bs4 import BeautifulSoup

# KLIP-AVATAR -> KLIPAURA repo root
_KLIP_AVATAR_ROOT = os.path.dirname(_CORE)
_REPO_ROOT = os.path.dirname(_KLIP_AVATAR_ROOT)

ASSETS_DIR = os.path.join(_REPO_ROOT, "assets", "product")
OUTPUT_VIDEO = os.path.join(_REPO_ROOT, "output", "video.mp4")

W = 1080
H = 1920
FPS = 30
MAX_CUT_SEC = 1.8
PRODUCT_BAND_MIN = 0.042

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _normalize_url(url: str) -> str:
    u = (url or "").strip()
    if u.startswith("//"):
        u = "https:" + u
    p = urlparse(u)
    if not p.scheme:
        return u
    return urlunparse((p.scheme, p.netloc, p.path, "", "", ""))


def _first_img_url(img, base: str) -> str:
    for key in (
        "src",
        "data-src",
        "data-lazy",
        "data-lazy-src",
        "data-original",
        "data-zoom",
    ):
        raw = (img.get(key) or "").strip()
        if raw and raw not in ("undefined", "null", "about:blank"):
            return raw
    srcset = (img.get("srcset") or "").strip()
    if srcset:
        part = srcset.split(",")[0].strip().split()[0]
        if part.startswith("http") or part.startswith("/"):
            return part
    return ""


def _abs_url(raw: str, base: str) -> str | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw.startswith("//"):
        raw = "https:" + raw
    elif raw.startswith("/"):
        raw = urljoin(base, raw)
    elif not raw.startswith("http"):
        raw = urljoin(base, raw)
    if "http" not in raw:
        return None
    return raw


def _kwcdn_from_html(html: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    patterns = (
        r"https://img\.kwcdn\.com/[a-zA-Z0-9_\-./%]+\.(?:jpg|jpeg|png|webp)(?:\?[^\s\"'<>]*)?",
        # Temu sometimes embeds URLs without extension in the captured slice
        r"https://img\.kwcdn\.com/[a-zA-Z0-9_\-./%?&=]+",
    )
    for pat in patterns:
        for m in re.finditer(pat, html, re.I):
            u = _normalize_url(m.group(0).rstrip(")'\",; "))
            if "kwcdn.com" not in u or u in seen:
                continue
            if not any(x in u.lower() for x in (".jpg", ".jpeg", ".png", ".webp")):
                continue
            seen.add(u)
            out.append(u)
    return out


def _video_urls_from_html(html: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for m in re.finditer(
        r"https?://[^\s\"'<>]+\.(?:mp4|webm)(?:\?[^\s\"'<>]*)?",
        html,
        re.I,
    ):
        u = _normalize_url(m.group(0))
        if u not in seen and "kwcdn" in u.lower():
            seen.add(u)
            out.append(u)
    return out


def _resolution_score(url: str) -> int:
    m = re.search(r"(\d{3,4})\s*[xX*]\s*(\d{3,4})", url)
    if m:
        return int(m.group(1)) * int(m.group(2))
    if re.search(r"original|upload/[a-f0-9-]{8,}", url, re.I):
        return 1_500_000
    return len(url)


def scrape_temu_page(product_url: str, timeout: float = 25.0) -> dict[str, Any]:
    """Fetch HTML; hero + gallery + video URLs (normalized, deduped)."""
    headers = {**_DEFAULT_HEADERS, "Referer": f"https://{urlparse(product_url).netloc}/"}
    res = requests.get(product_url, timeout=timeout, headers=headers)
    res.raise_for_status()
    html = res.text
    soup = BeautifulSoup(html, "html.parser")
    base = product_url
    seen: set[str] = set()
    gallery: list[str] = []

    for img in soup.find_all("img"):
        raw = _first_img_url(img, base)
        au = _abs_url(raw, base)
        if not au:
            continue
        nu = _normalize_url(au)
        if nu in seen:
            continue
        seen.add(nu)
        gallery.append(nu)

    for u in _kwcdn_from_html(html):
        nu = _normalize_url(u)
        if nu not in seen:
            seen.add(nu)
            gallery.append(nu)

    hero = ""
    if gallery:
        hero = max(gallery, key=_resolution_score)
    videos = [_normalize_url(v) for v in _video_urls_from_html(html)]

    title = ""
    if soup.title:
        title = (soup.title.get_text(strip=True) or "")[:500]

    return {
        "title": title,
        "hero_url": hero,
        "gallery": gallery,
        "video_urls": videos,
        "html_len": len(html),
    }


def download_binary(url: str, dest: str, timeout: float = 60.0) -> bool:
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    headers = {**_DEFAULT_HEADERS, "Referer": "https://www.temu.com/"}
    r = requests.get(url, timeout=timeout, headers=headers, stream=True)
    r.raise_for_status()
    with open(dest, "wb") as f:
        for chunk in r.iter_content(65536):
            if chunk:
                f.write(chunk)
    return os.path.isfile(dest) and os.path.getsize(dest) > 100


def _pick_windows_bold_font() -> str:
    p = r"C:\Windows\Fonts\arialbd.ttf"
    if os.path.isfile(p):
        return p
    return ""


def _ensure_bold_font_copy(wd: str) -> str:
    """Copy Arial Bold into ``wd`` so drawtext can use ``fontfile=_fb.ttf`` (no drive letter in filter)."""
    dest = os.path.join(wd, "_fb.ttf")
    src = _pick_windows_bold_font()
    if src and (not os.path.isfile(dest) or os.path.getsize(dest) < 1000):
        try:
            shutil.copy2(src, dest)
        except OSError:
            pass
    return "_fb.ttf" if os.path.isfile(dest) else ""


def _write_caption_file(work_dir: str, basename: str, content: str) -> str:
    """Write UTF-8 caption next to clip output; use basename-only in drawtext (no ``C:`` in filter)."""
    os.makedirs(work_dir, exist_ok=True)
    path = os.path.join(work_dir, basename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return basename


def _image_to_clip(
    ffmpeg: str,
    image_path: str,
    out_mp4: str,
    duration: float,
    line1: str,
    line2: str | None,
    *,
    y_main: int = 150,
) -> bool:
    """Ken Burns + micro wobble + bold-style drawtext (two lines optional)."""
    dur = max(0.35, float(duration))
    dframes = max(1, int(round(FPS * dur)))
    dm1 = max(1, dframes - 1)
    wd = os.path.dirname(os.path.abspath(out_mp4)) or "."
    _fb = _ensure_bold_font_copy(wd)
    font_arg = f"fontfile={_fb}" if _fb else "font=Arial"
    # Slow zoom 1 -> ~1.06 over clip; micro pan
    zp = (
        f"zoompan=z='min(1+0.06*on/{dm1},1.06)':d={dframes}:"
        f"x='iw/2-(iw/zoom/2)+14*sin(6.2831853*on/{dframes})':"
        f"y='ih/2-(ih/zoom/2)+8*sin(3.14159265*on/{dframes})':"
        f"s={W}x{H}:fps={FPS}"
    )
    wobble = (
        "pad=iw+8:ih+8:4:4,"
        "crop=iw-8:ih-8:4+3*sin(6.2831853*t*5.5):4+3*cos(6.2831853*t*4.7)"
    )
    b1 = f"cap_{os.path.splitext(os.path.basename(out_mp4))[0]}_a.txt"
    _write_caption_file(wd, b1, line1)
    dt = (
        f"drawtext={font_arg}:textfile={b1}:fontsize=52:fontcolor=white:"
        f"borderw=4:bordercolor=black:x=(w-text_w)/2:y={y_main}:"
        "shadowcolor=black@0.85:shadowx=3:shadowy=3"
    )
    if line2:
        b2 = f"cap_{os.path.splitext(os.path.basename(out_mp4))[0]}_b.txt"
        _write_caption_file(wd, b2, line2)
        dt += (
            f",drawtext={font_arg}:textfile={b2}:fontsize=46:fontcolor=white:"
            f"borderw=4:bordercolor=black:x=(w-text_w)/2:y={y_main + 62}:"
            "shadowcolor=black@0.85:shadowx=3:shadowy=3"
        )
    vf = (
        f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
        f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,{zp},{wobble},{dt}"
    )
    r = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-loop",
            "1",
            "-i",
            image_path,
            "-t",
            f"{dur:.4f}",
            "-vf",
            vf,
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(FPS),
            out_mp4,
        ],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=wd,
    )
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "")[-2000:]
        print(f"  ffmpeg image clip stderr: {err}", flush=True)
    return r.returncode == 0 and os.path.isfile(out_mp4) and os.path.getsize(out_mp4) > 2000


def _video_segment_to_clip(
    ffmpeg: str,
    video_path: str,
    out_mp4: str,
    ss: float,
    duration: float,
    line1: str,
    line2: str | None,
) -> bool:
    """1–2s re-encoded segment with scale/crop + motion boost + text."""
    dur = max(0.35, min(float(duration), MAX_CUT_SEC))
    dframes = max(1, int(round(FPS * dur)))
    dm1 = max(1, dframes - 1)
    wd = os.path.dirname(os.path.abspath(out_mp4)) or "."
    _fb = _ensure_bold_font_copy(wd)
    font_arg = f"fontfile={_fb}" if _fb else "font=Arial"
    zp = (
        f"zoompan=z='min(1+0.04*on/{dm1},1.08)':d={dframes}:"
        f"x='iw/2-(iw/zoom/2)+18*sin(6.2831853*on/{dframes})':"
        f"y='ih/2-(ih/zoom/2)+12*cos(6.2831853*on/{dframes})':"
        f"s={W}x{H}:fps={FPS}"
    )
    wobble = (
        "pad=iw+8:ih+8:4:4,"
        "crop=iw-8:ih-8:4+3*sin(6.2831853*t*6.2):4+3*cos(6.2831853*t*5.1)"
    )
    b1 = f"cap_{os.path.splitext(os.path.basename(out_mp4))[0]}_a.txt"
    _write_caption_file(wd, b1, line1)
    dt = (
        f"drawtext={font_arg}:textfile={b1}:fontsize=52:fontcolor=white:"
        f"borderw=4:bordercolor=black:x=(w-text_w)/2:y=150:"
        "shadowcolor=black@0.85:shadowx=3:shadowy=3"
    )
    if line2:
        b2 = f"cap_{os.path.splitext(os.path.basename(out_mp4))[0]}_b.txt"
        _write_caption_file(wd, b2, line2)
        dt += (
            f",drawtext={font_arg}:textfile={b2}:fontsize=46:fontcolor=white:"
            f"borderw=4:bordercolor=black:x=(w-text_w)/2:y=212:"
            "shadowcolor=black@0.85:shadowx=3:shadowy=3"
        )
    vf = (
        f"scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},setsar=1,{zp},{wobble},{dt}"
    )
    r = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-ss",
            f"{max(0.0, ss):.3f}",
            "-i",
            video_path,
            "-t",
            f"{dur:.4f}",
            "-vf",
            vf,
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(FPS),
            out_mp4,
        ],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=wd,
    )
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "")[-1800:]
        print(f"  ffmpeg video clip stderr: {err}", flush=True)
    return r.returncode == 0 and os.path.isfile(out_mp4) and os.path.getsize(out_mp4) > 2000


def _concat_and_eq_audio(
    ffmpeg: str,
    ffprobe: str,
    concat_list_path: str,
    out_path: str,
) -> bool:
    """Concat demuxer → eq pulse → silent stereo audio."""
    tmp = out_path + "._pre_eq.mp4"
    r0 = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            concat_list_path,
            "-c",
            "copy",
            tmp,
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if r0.returncode != 0 or not os.path.isfile(tmp):
        return False
    dur = 1.0
    try:
        pr = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", tmp],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if pr.returncode == 0 and pr.stdout.strip():
            dur = float(pr.stdout.strip().split()[0])
    except (ValueError, IndexError):
        pass
    eq_vf = "eq=contrast=1.06:brightness='0.028*sin(5.3*t)':eval=frame,format=yuv420p"
    r = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            tmp,
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=channel_layout=stereo:sample_rate=48000",
            "-vf",
            eq_vf,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-shortest",
            "-t",
            f"{dur + 0.05:.3f}",
            out_path,
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    try:
        os.unlink(tmp)
    except OSError:
        pass
    return r.returncode == 0 and os.path.isfile(out_path) and os.path.getsize(out_path) > 5000


def run() -> int:
    from services.influencer_engine.rendering.ffmpeg_path import get_ffmpeg_exe, get_ffprobe_exe
    from engine.ugc_final_render_validation import get_product_band_mad_scores

    product_url = (os.environ.get("PRODUCT_URL") or "").strip() or (
        "https://www.temu.com/ae/2025-new-blackhead-remover-with-facial-pore-vacuum-pore-cleaner-3-levels-of-suction-5-attachments-usb-rechargeable-facial-cleaning-kit-electric-tool-for-adult-skincare--birthdays-and-mothers-day-g-601099525965720.html"
    )
    _ = (os.environ.get("AFFILIATE_URL") or "").strip() or "https://temu.to/k/er4dcds75ro"
    price_text = (os.environ.get("PRICE_TEXT") or "").strip() or (
        "🎉 Coupon price[AED13.98]\n⚠️ The discount may vary, please refer to the page display."
    )

    split_raw = (os.environ.get("AFFILIATE_SPLIT_TOP_RATIO") or "").strip()
    print(
        "AFFILIATE_SPLIT_TOP_RATIO=" + (repr(split_raw) if split_raw else "(unset, default 0.30 in engine)"),
        flush=True,
    )

    ffmpeg = get_ffmpeg_exe()
    ffprobe = get_ffprobe_exe()

    os.makedirs(ASSETS_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(OUTPUT_VIDEO) or ".", exist_ok=True)

    _ugc_only = (os.environ.get("UGC_PRODUCT_IMAGE_URLS") or "").strip()
    if _ugc_only:
        print("[1/4] UGC_PRODUCT_IMAGE_URLS set — skip Temu scrape; use listed URLs only", flush=True)
        gallery = [_normalize_url(x.strip()) for x in _ugc_only.split(",") if x.strip()]
        vids = []
    else:
        print("[1/4] Scrape Temu…", flush=True)
        data = scrape_temu_page(product_url)
        gallery = list(data.get("gallery") or [])
        vids = list(data.get("video_urls") or [])
        _manual = (os.environ.get("TEMU_SHORTFORM_IMAGE_URLS") or "").strip()
        if _manual:
            for u in [x.strip() for x in _manual.split(",") if x.strip()]:
                nu = _normalize_url(u)
                if nu not in gallery:
                    gallery.append(nu)
    # Highest-res first (hero) for scene 1 close-up
    gallery.sort(key=_resolution_score, reverse=True)

    if not gallery and not vids:
        print(
            "ERROR: No kwcdn images or video URLs found. "
            "Temu may be serving a bot page — open the product in a browser, "
            "copy img.kwcdn.com URLs, set UGC_PRODUCT_IMAGE_URLS (see product_extractor) "
            "or retry with network access.",
            flush=True,
        )
        return 2

    assets_used: list[str] = []

    # Download gallery
    local_images: list[str] = []
    for i, u in enumerate(gallery):
        ext = ".jpg"
        low = u.lower()
        if ".png" in low:
            ext = ".png"
        elif ".webp" in low:
            ext = ".webp"
        dest = os.path.join(ASSETS_DIR, f"gallery_{i:02d}{ext}")
        try:
            if download_binary(u, dest):
                local_images.append(dest)
                assets_used.append(u)
        except Exception as e:
            print(f"  WARN skip image {i}: {e}", flush=True)
    if local_images:
        hero_dest = os.path.join(ASSETS_DIR, f"hero{os.path.splitext(local_images[0])[1] or '.jpg'}")
        try:
            shutil.copy2(local_images[0], hero_dest)
        except OSError:
            pass

    local_video = ""
    if vids:
        destv = os.path.join(ASSETS_DIR, "product_embedded.mp4")
        try:
            if download_binary(vids[0], destv):
                local_video = destv
                assets_used.append(vids[0])
        except Exception as e:
            print(f"  WARN video download: {e}", flush=True)

    if not local_images:
        print("ERROR: No images downloaded — cannot build video without hallucinating assets.", flush=True)
        return 3

    m_price = re.search(r"AED\s*([\d.]+)", price_text.replace(",", "."), re.I)
    aed = m_price.group(1) if m_price else "13.98"
    line_cta1 = f"AED {aed} 😳"
    line_cta2 = "Link in bio"

    work = tempfile.mkdtemp(prefix="temu_short_")
    try:
        clips: list[str] = []
        img0 = local_images[0]
        nimg = len(local_images)
        # Scene / sub-cut plan (all cuts ≤ 1.8s; total 10s): 1+1 + 1.5+1.5 + 1.5+1.5 + 1+1
        for i in range(8):
            out_clip = os.path.join(work, f"c{i:02d}.mp4")
            if i in (0, 1):
                dur = 1.0
                src = img0 if i == 0 else local_images[min(1, nimg - 1)]
                ok = _image_to_clip(
                    ffmpeg,
                    src,
                    out_clip,
                    dur,
                    "This actually pulls it out 😳",
                    None,
                )
            elif i in (2, 3):
                dur = 1.5
                if local_video and os.path.isfile(local_video):
                    ss = 0.35 + (1.5 * (i - 2))
                    ok = _video_segment_to_clip(
                        ffmpeg,
                        local_video,
                        out_clip,
                        ss,
                        dur,
                        "3 suction levels + 5 heads",
                        None,
                    )
                else:
                    src = local_images[min(2 + (i - 2), nimg - 1)]
                    ok = _image_to_clip(ffmpeg, src, out_clip, dur, "3 suction levels + 5 heads", None)
            elif i in (4, 5):
                dur = 1.5
                src = local_images[min(3 + (i - 4), nimg - 1)]
                ok = _image_to_clip(
                    ffmpeg,
                    src,
                    out_clip,
                    dur,
                    "USB rechargeable. Clean skin in minutes",
                    None,
                )
            else:
                dur = 1.0
                src = local_images[min(5, nimg - 1)]
                ok = _image_to_clip(ffmpeg, src, out_clip, dur, line_cta1, line_cta2, y_main=120)
            if not ok:
                print(f"ERROR: clip {i} failed", flush=True)
                return 4
            clips.append(out_clip)

        lst = os.path.join(work, "concat.txt")
        with open(lst, "w", encoding="utf-8") as f:
            for c in clips:
                posix = Path(c).resolve().as_posix()
                f.write(f"file '{posix}'\n")

        print("[2/4] Concat + EQ + audio…", flush=True)
        if not _concat_and_eq_audio(ffmpeg, ffprobe, lst, OUTPUT_VIDEO):
            print("ERROR: final encode failed", flush=True)
            return 5

        print("[3/4] MAD metrics…", flush=True)
        m12, m23, band = get_product_band_mad_scores(OUTPUT_VIDEO, ffmpeg, ffprobe)
        print(
            f"MAD m12={m12} m23={m23} band={band:.4f} (gate max_pair>={PRODUCT_BAND_MIN})",
            flush=True,
        )
        ok_gate = (
            m12 is not None
            and m23 is not None
            and max(m12, m23) >= PRODUCT_BAND_MIN
        )
        print(f"MAD_GATE_OK={ok_gate}", flush=True)

        n_assets = len(set(assets_used))
        print(f"ASSET_COUNT_USED={n_assets} (unique source URLs)", flush=True)
        print("ASSETS_USED_URLS:", flush=True)
        for u in sorted(set(assets_used)):
            print(f"  {u}", flush=True)
        print("ASSETS_LOCAL:", flush=True)
        for p in local_images:
            print(f"  {os.path.abspath(p)}", flush=True)
        if local_video and os.path.isfile(local_video):
            print(f"  {os.path.abspath(local_video)}", flush=True)
        print("[4/4] Done.", flush=True)
        print(f"OUTPUT={os.path.abspath(OUTPUT_VIDEO)}", flush=True)
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(run())
