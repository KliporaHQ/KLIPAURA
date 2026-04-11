#!/usr/bin/env python3
"""
KLIP-AVATAR Core V1 Production Entry Point
Clean production startup for Mission Control.
DO NOT add emojis or noisy prints.
"""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    raise RuntimeError("python-dotenv is required: pip install python-dotenv")

env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(env_path)

os.chdir(Path(__file__).resolve().parent)

from config import OUTPUT_DIR, validate_required_environment

validate_required_environment()

output_dir = OUTPUT_DIR
output_dir.mkdir(parents=True, exist_ok=True)

print("KLIP-AVATAR Core V1.5 starting (standalone mode)")

from api.server import app

if __name__ == "__main__":
    import uvicorn
    print(f"KLIP-AVATAR Core V1 listening on port {os.getenv('PORT', 8000)}")

    uvicorn.run(
        "api.server:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
        log_level="info",
        workers=int(os.getenv("WEB_CONCURRENCY", 1))
    )
