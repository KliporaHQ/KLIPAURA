"""
KLIP-CORE Structured Logging
=============================
Production-ready JSON logging with context.
"""

from __future__ import annotations
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Union


# stdlib LogRecord attribute names — cannot be used in `Logger.log(..., extra=...)`
_LOGRECORD_EXTRA_FORBIDDEN = frozenset(
    {
        "name",
        "msg",
        "args",
        "created",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "thread",
        "threadName",
        "exc_info",
        "exc_text",
        "stack_info",
        "taskName",
    }
)


def _stdlib_safe_extra(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Rename keys that collide with LogRecord so stdlib logging does not raise."""
    out: Dict[str, Any] = {}
    for k, v in entry.items():
        if k in _LOGRECORD_EXTRA_FORBIDDEN:
            out[f"ctx_{k}"] = v
        else:
            out[k] = v
    return out


# ─── Log Levels ───────────────────────────────────────────────────────────────

class LogLevel:
    DEBUG = "DEBUG"
    INFO = "INFO"
    SUCCESS = "SUCCESS"  # Custom level
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


# ─── Colors for Console ────────────────────────────────────────────────────────

COLORS = {
    "DEBUG": "\033[36m",     # Cyan
    "INFO": "\033[34m",      # Blue
    "SUCCESS": "\033[32m",   # Green
    "WARNING": "\033[33m",   # Yellow
    "ERROR": "\033[31m",     # Red
    "CRITICAL": "\033[35m",  # Magenta
    "RESET": "\033[0m",
}


# ─── Logger Class ─────────────────────────────────────────────────────────────

class KLIPLogger:
    """
    Structured logger with JSON output option.
    
    Features:
    - JSON structured logs for production
    - Colored console output for development
    - Context injection (module, job_id, etc.)
    - Stage progress tracking
    """
    
    def __init__(
        self,
        name: str = "klipaura",
        module: Optional[str] = None,
        job_id: Optional[str] = None,
        json_output: bool = False,
    ):
        self.name = name
        self.module = module or os.getenv("MODULE_NAME", "unknown")
        self.job_id = job_id
        self.json_output = json_output or os.getenv("LOG_JSON", "false").lower() == "true"
        self._context: Dict[str, Any] = {}
        
        # Setup Python logger
        self._logger = logging.getLogger(name)
        self._logger.setLevel(getattr(logging, os.getenv("LOG_LEVEL", "INFO")))
        
        if not self._logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(logging.Formatter("%(message)s"))
            self._logger.addHandler(handler)
    
    def set_context(self, **kwargs) -> None:
        """Add context to all subsequent log entries."""
        self._context.update(kwargs)
    
    def clear_context(self) -> None:
        """Clear all context."""
        self._context = {}
    
    def _format_log(
        self,
        level: str,
        message: str,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Format a structured log entry."""
        entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": level,
            # "module" is reserved on stdlib LogRecord.extra — use ctx_module for JSON/console
            "ctx_module": self.module,
            "logger": self.name,
            "message": message,
        }
        
        if self.job_id:
            entry["job_id"] = self.job_id
        
        entry.update(self._context)
        
        if extra:
            entry.update(extra)
        
        return entry
    
    def _output(self, level: str, entry: Dict[str, Any]) -> None:
        """Output a log entry."""
        if self.json_output:
            print(json.dumps(entry), flush=True)
        else:
            color = COLORS.get(level, "")
            reset = COLORS["RESET"]
            ts = entry["timestamp"][11:19]
            module = entry.get("ctx_module", "")
            msg = entry["message"]
            print(f"{color}[{ts}] [{level:8}] [{module}] {msg}{reset}", flush=True)
    
    def debug(self, message: str, **kwargs) -> None:
        """Log a debug message."""
        entry = self._format_log("DEBUG", message, kwargs)
        self._logger.debug(message, extra=_stdlib_safe_extra(entry))
        self._output("DEBUG", entry)
    
    def info(self, message: str, **kwargs) -> None:
        """Log an info message."""
        entry = self._format_log("INFO", message, kwargs)
        self._logger.info(message, extra=_stdlib_safe_extra(entry))
        self._output("INFO", entry)
    
    def success(self, message: str, **kwargs) -> None:
        """Log a success message."""
        entry = self._format_log("SUCCESS", message, kwargs)
        self._logger.info(message, extra=_stdlib_safe_extra(entry))
        self._output("SUCCESS", entry)
    
    def warning(self, message: str, **kwargs) -> None:
        """Log a warning message."""
        entry = self._format_log("WARNING", message, kwargs)
        self._logger.warning(message, extra=_stdlib_safe_extra(entry))
        self._output("WARNING", entry)
    
    def error(self, message: str, **kwargs) -> None:
        """Log an error message."""
        entry = self._format_log("ERROR", message, kwargs)
        self._logger.error(message, extra=_stdlib_safe_extra(entry))
        self._output("ERROR", entry)
    
    def critical(self, message: str, **kwargs) -> None:
        """Log a critical message."""
        entry = self._format_log("CRITICAL", message, kwargs)
        self._logger.critical(message, extra=_stdlib_safe_extra(entry))
        self._output("CRITICAL", entry)
    
    def log(self, level: str, message: str, **kwargs) -> None:
        """Log with custom level."""
        getattr(self, level.lower(), self.info)(message, **kwargs)


# ─── Module-Level Functions ────────────────────────────────────────────────────

_loggers: Dict[str, KLIPLogger] = {}


def get_logger(
    name: str = "klipaura",
    module: Optional[str] = None,
    **kwargs
) -> KLIPLogger:
    """
    Get or create a logger instance.
    
    Args:
        name: Logger name (usually module name)
        module: Module context
        **kwargs: Additional context
    
    Returns:
        KLIPLogger instance
    
    Example:
        logger = get_logger("avatar")
        logger.info("Processing video", job_id="123")
    """
    key = f"{name}:{module or 'default'}"
    
    if key not in _loggers:
        _loggers[key] = KLIPLogger(name=name, module=module, **kwargs)
    
    return _loggers[key]


def log_structured(
    module: str,
    message: str,
    level: str = "INFO",
    **kwargs
) -> None:
    """
    Quick structured logging function.
    
    Args:
        module: Module name
        message: Log message
        level: Log level
        **kwargs: Additional structured data
    
    Example:
        log_structured("avatar", "Video completed", level="SUCCESS", 
                      job_id="123", video_url="...")
    """
    logger = get_logger(module=module)
    getattr(logger, level.lower())(message, **kwargs)


def log_stage(
    stage: str,
    message: str,
    progress: Optional[int] = None,
    module: Optional[str] = None,
) -> None:
    """
    Log a pipeline stage update.
    
    Args:
        stage: Stage name (e.g., "SCRIPT", "RENDER")
        message: Stage message
        progress: Optional progress percentage (0-100)
        module: Module name
    
    Example:
        log_stage("SCRIPT", "Starting script generation", 10)
    """
    logger = get_logger(module=module)
    data = {"stage": stage}
    
    if progress is not None:
        data["progress"] = progress
    
    logger.info(f"[{stage}] {message}", **data)


def log_error(
    module: str,
    message: str,
    error: Optional[Union[str, Exception]] = None,
    **kwargs
) -> None:
    """
    Log an error with optional exception details.
    
    Args:
        module: Module name
        message: Error message
        error: Exception object or error string
        **kwargs: Additional data
    
    Example:
        try:
            risky_operation()
        except Exception as e:
            log_error("avatar", "Pipeline failed", e)
    """
    logger = get_logger(module=module)
    
    data = {}
    if error:
        if isinstance(error, Exception):
            data["error_type"] = type(error).__name__
            data["error_message"] = str(error)
        else:
            data["error"] = str(error)
    
    data.update(kwargs)
    logger.error(message, **data)
