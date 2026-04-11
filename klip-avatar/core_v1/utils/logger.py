from .state import state
from datetime import datetime
import json
import os

def log(message: str, stage: str = "INFO"):
    """Log message to state and console (human readable)."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    full_msg = f"[{timestamp}] [{stage}] {message}"
    state.add_log(full_msg, stage)
    return full_msg

def log_stage(stage_name: str, message: str, progress: int = None):
    """Log for specific stage with progress update."""
    if progress is not None:
        state.update(progress=progress, stage=stage_name.lower())
    log(message, stage_name)

def log_structured(stage: str, message: str, level: str = "info", **kwargs):
    """Structured log for production."""
    entry = {
        "timestamp": datetime.now().isoformat(),
        "stage": stage,
        "level": level,
        "message": message,
        **kwargs
    }
    # Keep human readable too
    log(f"{message} {json.dumps(kwargs)}", stage.upper())
    # Could save to file if needed
    return entry

def log_error(stage: str, error: str, details=None):
    """Log errors with state update."""
    log_structured(stage, f"ERROR: {error}", "error", details=details)
    state.update(error=error, status="failed")
