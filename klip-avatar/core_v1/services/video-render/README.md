# Video Render Service — FFmpeg (no Shotstack)

Renders vertical short videos (1080×1920, 30fps, MP4) from:

- **Images** → zoompan motion → concatenated scenes  
- **Voice** → audio overlay  
- Optional: captions burn-in, background music  

Used by Mission Control or Railway render service. **Max retries: 3.**

## Usage

```python
from services.video_render import render_video, RenderInput

result = render_video(RenderInput(
    job_id="job_123",
    image_urls=["/tmp/s1.png", "/tmp/s2.png"],
    voice_path="/tmp/voice.mp3",
    duration_per_scene=5.0,
))
# result["success"], result["output_path"], result["error"]
```

## Pipeline

1. Per-image zoompan (scale to 1080x1920, pad, zoompan filter).  
2. Concat all scenes.  
3. Mix voice (and optional music).  
4. Output: libx264 + aac, 30fps.

## Requirements

- **FFmpeg** on PATH (or set `ffmpeg_path`).  
- Images and voice as **local paths** (caller downloads from URLs if needed).

## Integration

- **Railway:** Deploy this as a small HTTP service that accepts POST `/render` with job payload, downloads assets, calls `render_video()`, uploads result to S3, returns URL.  
- **Mission Control:** Can call Railway render URL (existing `_call_railway_render`) or run this module in-process if FFmpeg is available on the host.
