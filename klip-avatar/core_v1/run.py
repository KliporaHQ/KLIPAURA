#!/usr/bin/env python3
"""
KLIP-AVATAR Core V1 Runner
Starts the FastAPI server for the new Mission Control V4.
"""
import os
import uvicorn
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

if __name__ == "__main__":
    print("Starting KLIP-AVATAR Core V1 (dev mode)...")
    uvicorn.run(
        "api.server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=[str(Path(__file__).resolve().parent)],
        log_level="info"
    )
