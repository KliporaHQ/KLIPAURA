"""
Treat obvious .env placeholder strings as "not configured".

Dashboard and health checks use this so `API_KEY=your-key-here` does not look "set".
"""

from __future__ import annotations

_PLACEHOLDER_TOKENS = frozenset(
    {
        "your-key-here",
        "your_key_here",
        "your-api-key-here",
        "your_api_key_here",
        "changeme",
        "change-me",
        "change_me",
        "placeholder",
        "xxx",
        "todo",
        "replace-me",
        "replace_me",
        "insert-key-here",
        "api-key-here",
        "sk-your-openai-api-key-here",
        "paste-your-key",
    }
)


def is_configured_secret(raw: str | None) -> bool:
    """True if non-empty and not an obvious documentation placeholder."""
    if raw is None:
        return False
    s = raw.strip()
    if not s:
        return False
    norm = s.lower().strip("\"'")
    norm_compact = norm.replace(" ", "").replace("_", "-")
    if norm_compact in _PLACEHOLDER_TOKENS or norm in _PLACEHOLDER_TOKENS:
        return False
    if norm_compact.startswith("your-") and norm_compact.endswith("-here"):
        return False
    return True
