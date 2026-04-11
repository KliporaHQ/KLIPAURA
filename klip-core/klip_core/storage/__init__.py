"""
KLIP-CORE Storage Module
========================
R2 (Cloudflare) and local file storage utilities.
"""

from .r2 import (
    r2_configured,
    upload_to_r2,
    download_from_r2,
    get_r2_client,
    create_r2_store,
    R2Store,
)

__all__ = [
    "r2_configured",
    "upload_to_r2",
    "download_from_r2",
    "get_r2_client",
    "create_r2_store",
    "R2Store",
]
