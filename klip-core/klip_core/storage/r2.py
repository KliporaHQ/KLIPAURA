"""
KLIP-CORE R2 Storage
====================
Cloudflare R2 integration for storing videos, images, and assets.
"""

from __future__ import annotations
import os
from pathlib import Path
from typing import Optional

from ..config import get_settings


# ─── R2 Configuration Check ───────────────────────────────────────────────────

def r2_configured() -> bool:
    """
    Check if object storage is properly configured.

    Requires access keys. For Cloudflare R2 set ``R2_ACCOUNT_ID`` (or ``R2_ENDPOINT``).
    For MinIO set ``R2_ENDPOINT`` + keys (account id may be omitted).
    """
    settings = get_settings()
    keys = bool(settings.r2_access_key_id and settings.r2_secret_access_key)
    if not keys:
        return False
    ep = (settings.r2_endpoint or os.getenv("R2_ENDPOINT") or "").strip()
    aid = (settings.r2_account_id or "").strip()
    return bool(ep or aid)


# ─── R2 Client ─────────────────────────────────────────────────────────────────

def get_r2_client() -> Optional[R2Store]:
    """
    Get an R2 client if configured.
    
    Returns:
        R2Store instance or None if not configured.
    """
    if not r2_configured():
        return None
    
    return create_r2_store()


def create_r2_store() -> R2Store:
    """
    Create a new R2 store instance.
    
    Returns:
        R2Store configured with environment variables.
    """
    settings = get_settings()
    endpoint = (settings.r2_endpoint or os.getenv("R2_ENDPOINT") or "").strip() or None
    public_base = (settings.r2_public_url or os.getenv("R2_PUBLIC_URL") or "").strip() or None

    return R2Store(
        account_id=settings.r2_account_id or "",
        access_key_id=settings.r2_access_key_id or "",
        secret_access_key=settings.r2_secret_access_key or "",
        bucket=settings.r2_bucket,
        endpoint_url=endpoint,
        public_url_base=public_base,
    )


# ─── R2 Store Class ────────────────────────────────────────────────────────────

class R2Store:
    """
    Cloudflare R2 storage client.
    
    R2 is S3-compatible, so we use boto3-style API.
    
    Usage:
        store = R2Store(...)
        store.upload("/local/video.mp4", "videos/video.mp4")
        url = store.get_public_url("videos/video.mp4")
    """
    
    def __init__(
        self,
        account_id: str,
        access_key_id: str,
        secret_access_key: str,
        bucket: str = "klipaura",
        public_url_base: Optional[str] = None,
        endpoint_url: Optional[str] = None,
    ):
        self.account_id = account_id
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.bucket = bucket
        # Public URL for uploaded objects (R2 custom domain, r2.dev, or MinIO console URL)
        self.public_url_base = (public_url_base or "").rstrip("/") or (
            f"https://{bucket}.{account_id}.r2.dev" if account_id else ""
        )
        # S3 API endpoint — never use r2.dev for boto3; use R2_ENDPOINT or Cloudflare default
        ep = (endpoint_url or os.getenv("R2_ENDPOINT") or "").strip()
        if ep:
            self._endpoint_url = ep.rstrip("/")
        elif account_id:
            self._endpoint_url = f"https://{account_id}.r2.cloudflarestorage.com"
        else:
            self._endpoint_url = ""
        
        self._client = None
    
    @property
    def client(self):
        """Get or create the S3 client."""
        if self._client is None:
            try:
                import boto3
                if not self._endpoint_url:
                    raise ValueError("R2 endpoint missing: set R2_ENDPOINT or R2_ACCOUNT_ID")
                self._client = boto3.client(
                    "s3",
                    endpoint_url=self._endpoint_url,
                    aws_access_key_id=self.access_key_id,
                    aws_secret_access_key=self.secret_access_key,
                    region_name="auto",
                )
            except ImportError:
                raise ImportError(
                    "boto3 required for R2 storage. Install with: pip install boto3"
                )
        
        return self._client
    
    def upload(
        self,
        local_path: str,
        remote_key: str,
        content_type: Optional[str] = None,
    ) -> str:
        """
        Upload a file to R2.
        
        Args:
            local_path: Path to local file
            remote_key: R2 object key (e.g., "videos/video.mp4")
            content_type: MIME type (optional)
        
        Returns:
            Public URL of uploaded file.
        """
        extra_args = {}
        if content_type:
            extra_args["ContentType"] = content_type
        
        self.client.upload_file(
            local_path,
            self.bucket,
            remote_key,
            ExtraArgs=extra_args,
        )
        
        return f"{self.public_url_base}/{remote_key}"
    
    def upload_fileobj(
        self,
        file_obj,
        remote_key: str,
        content_type: Optional[str] = None,
    ) -> str:
        """
        Upload a file-like object to R2.
        """
        extra_args = {}
        if content_type:
            extra_args["ContentType"] = content_type
        
        self.client.upload_fileobj(
            file_obj,
            self.bucket,
            remote_key,
            ExtraArgs=extra_args,
        )
        
        return f"{self.public_url_base}/{remote_key}"
    
    def download(self, remote_key: str, local_path: str) -> bool:
        """
        Download a file from R2.
        
        Args:
            remote_key: R2 object key
            local_path: Where to save locally
        
        Returns:
            True if successful.
        """
        try:
            Path(local_path).parent.mkdir(parents=True, exist_ok=True)
            self.client.download_file(self.bucket, remote_key, local_path)
            return True
        except Exception:
            return False
    
    def download_to_path(self, remote_key: str, local_path: str) -> bool:
        """Alias for download()."""
        return self.download(remote_key, local_path)
    
    def delete(self, remote_key: str) -> bool:
        """
        Delete a file from R2.
        """
        try:
            self.client.delete_object(Bucket=self.bucket, Key=remote_key)
            return True
        except Exception:
            return False
    
    def exists(self, remote_key: str) -> bool:
        """
        Check if a file exists in R2.
        """
        try:
            self.client.head_object(Bucket=self.bucket, Key=remote_key)
            return True
        except Exception:
            return False
    
    def list(self, prefix: str = "") -> list[str]:
        """
        List objects in R2 with a prefix.
        
        Args:
            prefix: Filter prefix (e.g., "videos/")
        
        Returns:
            List of object keys.
        """
        try:
            response = self.client.list_objects_v2(
                Bucket=self.bucket,
                Prefix=prefix,
            )
            return [obj["Key"] for obj in response.get("Contents", [])]
        except Exception:
            return []
    
    def get_url(self, remote_key: str) -> str:
        """
        Get the public URL for an object.
        """
        return f"{self.public_url_base}/{remote_key}"
    
    def get_public_url(self, remote_key: str) -> str:
        """Alias for get_url()."""
        return self.get_url(remote_key)


# ─── Convenience Functions ──────────────────────────────────────────────────────

def upload_to_r2(
    local_path: str,
    remote_key: str,
    content_type: Optional[str] = None,
) -> Optional[str]:
    """
    Upload a file to R2.
    
    Returns:
        Public URL of uploaded file, or None if not configured.
    """
    store = get_r2_client()
    if store is None:
        return None
    
    return store.upload(local_path, remote_key, content_type)


def download_from_r2(remote_key: str, local_path: str) -> bool:
    """
    Download a file from R2.
    
    Returns:
        True if successful, False otherwise.
    """
    store = get_r2_client()
    if store is None:
        return False
    
    return store.download(remote_key, local_path)
