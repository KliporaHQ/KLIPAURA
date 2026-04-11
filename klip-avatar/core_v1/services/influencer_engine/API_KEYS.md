# AI Influencer Engine — API keys

## Where to add API keys

**File (the "container"):**

```
E:\KLIPORA\KLIPORA MASTER AUTOMATION\.env
```

Create this file if it doesn't exist (copy from `.env.example` in the same folder). The engine API, scripts, and pipeline load this `.env` when run from `KLIPORA MASTER AUTOMATION`.

---

## Your stack: Groq, Wavespeed, ffmpeg, optional ElevenLabs

| Purpose | Env variable | You use | Notes |
|--------|----------------|--------|------|
| **Script generation** (LLM) | `GROQ_API_KEY` | Yes | [Groq Console](https://console.groq.com) — script_agent uses this for video scripts. |
| **Video** | `WAVESPEED_API_KEY` | Yes | [Wavespeed](https://wavespeed.ai) — used by Mission Control / other workflows; engine uses **ffmpeg** for compose (static bg + audio). |
| **Video compose** | (ffmpeg) | Yes | No key; ensure `ffmpeg` is on PATH. Engine composes final clip with ffmpeg. |
| **Voice / TTS** | `ELEVENLABS_API_KEY` or `XI_API_KEY` | Optional | [ElevenLabs](https://elevenlabs.io) — real voice in sample videos; omit for mock audio. |
| **Avatar image** | `OPENAI_API_KEY` or `IMAGE_GEN_API_URL` | Optional | Real portrait from description; omit for gradient placeholder. |
| **Pipeline mode** | `INFLUENCER_ENGINE_MODE` | Set when testing | `mock` (default) or `production`. Use `production` when keys are set. |

---

## APIs to add (full reference)

| Purpose | Env variable | Where to get it |
|--------|----------------|------------------|
| Script (LLM) | `GROQ_API_KEY` | [Groq](https://console.groq.com) |
| Video | `WAVESPEED_API_KEY` | [Wavespeed](https://wavespeed.ai) |
| Voice (optional) | `ELEVENLABS_API_KEY` or `XI_API_KEY` | [ElevenLabs](https://elevenlabs.io) |
| Avatar image (optional) | `OPENAI_API_KEY` or `IMAGE_GEN_API_URL` | [OpenAI](https://platform.openai.com/api-keys) or your API |
| Mode | `INFLUENCER_ENGINE_MODE=production` | When you want real APIs used |
| Distribution (optional) | `TIKTOK_*`, `INSTAGRAM_*`, `YOUTUBE_*`, `X_API_KEY` / `TWITTER_*` | Platform dev consoles |

---

## Minimal .env for your stack

```env
GROQ_API_KEY=your-groq-key
WAVESPEED_API_KEY=your-wavespeed-key
# ELEVENLABS_API_KEY=your-elevenlabs-key
INFLUENCER_ENGINE_MODE=production
```

- **ffmpeg**: install locally (no env var); engine uses it for video compose.
- Add `ELEVENLABS_API_KEY` when you want real TTS in sample videos.
- Add `OPENAI_API_KEY` (or `IMAGE_GEN_API_URL`) when you want real avatar portraits.
