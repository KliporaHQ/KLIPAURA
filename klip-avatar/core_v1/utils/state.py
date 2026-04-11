import json
from typing import Dict, List, Any
import threading

class PipelineState:
    def __init__(self):
        self._state = {
            "stage": "idle",
            "progress": 0,
            "status": "idle",
            "logs": [],
            "output": "",
            "current_topic": "",
            "pipeline_mode": None,
            "video_config": None,
            "error": None,
            "durations": {},
            "started_at": None,
        }
        self._lock = threading.Lock()

    def update(self, **kwargs):
        with self._lock:
            self._state.update(kwargs)
            if "logs" not in kwargs and "log" in kwargs:
                self._state["logs"].append(kwargs["log"])
                if len(self._state["logs"]) > 100:
                    self._state["logs"] = self._state["logs"][-100:]

    def get(self) -> Dict:
        with self._lock:
            return self._state.copy()

    def add_log(self, message: str, stage: str = ""):
        log_entry = f"[{stage.upper()}] {message}" if stage else message
        self.update(log=log_entry)
        print(log_entry)

    def reset(self):
        with self._lock:
            self._state = {
                "stage": "idle",
                "progress": 0,
                "status": "idle",
                "logs": [],
                "output": "",
                "current_topic": "",
                "pipeline_mode": None,
                "video_config": None,
                "error": None,
                "durations": {},
                "started_at": None,
            }

state = PipelineState()
