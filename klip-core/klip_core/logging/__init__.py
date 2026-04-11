"""
KLIP-CORE Logging Module
========================
Structured JSON logging for production.
"""

from .structured import get_logger, log_structured, log_stage, log_error

__all__ = [
    "get_logger",
    "log_structured",
    "log_stage",
    "log_error",
]
