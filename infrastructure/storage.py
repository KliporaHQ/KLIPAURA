"""
S3-compatible asset store (AWS S3, MinIO, Cloudflare R2).

Originally extracted from legacy `s3_store.py`.
`upload_to_r2` is the thin helper used by the pipeline / job flow when R2 env is set.
"""

from __future__ import annotations

import os
from typing import Any, BinaryIO, List, Optional

try:
    import boto3
    from botocore.client import Config
    from botocore.exceptions import ClientError
except ImportError:
    boto3 = None
    Config = None  # type: ignore
    ClientError = Exception  # type: ignore

DEFAULT_R2_ENDPOINT = "https://3aed9aa011042cdc62ee367ec4a2f8e4.r2.cloudflarestorage.com"
DEFAULT_R2_BUCKET = "klipaura-master-2-0"


class S3AssetStore:
    """S3-compatible store. Endpoint + keys for R2; public_base_url for direct URLs."""

    def __init__(
        self,
        bucket: str,
        endpoint_url: Optional[str] = None,
        prefix: str = "assets",
        *,
        aws_access_key_id: Optional[str] = None,
        aws_secret_access_key: Optional[str] = None,
        region_name: str = "auto",
        public_base_url: Optional[str] = None,
    ) -> None:
        if not boto3:
            raise RuntimeError("boto3 required for S3 storage. Install with: pip install boto3")
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._public_base_url = (public_base_url or "").rstrip("/")

        use_ssl = True
        if endpoint_url and "r2.cloudflarestorage.com" in endpoint_url:
            use_ssl = endpoint_url.startswith("https://")
        config = Config(signature_version="s3v4", s3={"addressing_style": "path"})
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url or None,
            region_name=region_name,
            aws_access_key_id=aws_access_key_id or os.environ.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=aws_secret_access_key or os.environ.get("AWS_SECRET_ACCESS_KEY"),
            config=config,
            use_ssl=use_ssl,
        )

    def _key(self, key: str) -> str:
        if self._prefix:
            return f"{self._prefix}/{key.lstrip('/')}"
        return key.lstrip("/")

    def put(self, data: BinaryIO | bytes, content_type: str, *, key: str) -> str:
        k = self._key(key)
        extra = {"ContentType": content_type}
        if isinstance(data, bytes):
            self._client.put_object(Bucket=self._bucket, Key=k, Body=data, **extra)
        else:
            self._client.upload_fileobj(data, self._bucket, k, ExtraArgs=extra)
        return key

    def get_url(self, key: str) -> str:
        k = self._key(key)
        if self._public_base_url:
            return f"{self._public_base_url}/{k}"
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": k},
            ExpiresIn=3600,
        )

    def get_path(self, key: str) -> Optional[str]:
        return None

    def download_to_path(self, key: str, local_path: str) -> bool:
        try:
            self._client.download_file(self._bucket, self._key(key), local_path)
            return True
        except ClientError:
            return False

    def upload_file(self, local_path: str, key: str, content_type: Optional[str] = None) -> bool:
        try:
            extra: dict[str, Any] = {}
            if content_type:
                extra["ContentType"] = content_type
            self._client.upload_file(local_path, self._bucket, self._key(key), ExtraArgs=extra or None)
            return True
        except ClientError:
            return False

    def list_(self, prefix: str = "", limit: int = 100) -> List[dict]:
        out: List[dict] = []
        p = self._key(prefix)
        try:
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self._bucket, Prefix=p, MaxKeys=limit):
                for obj in page.get("Contents") or []:
                    out.append({
                        "key": obj["Key"].replace(self._prefix + "/", "", 1) if self._prefix else obj["Key"],
                        "size": obj.get("Size", 0),
                        "last_modified": (obj.get("LastModified") or 0).timestamp(),
                    })
                    if len(out) >= limit:
                        return out
        except ClientError:
            pass
        return out

    def delete(self, key: str) -> bool:
        try:
            self._client.delete_object(Bucket=self._bucket, Key=self._key(key))
            return True
        except ClientError:
            return False


def create_r2_store(
    bucket: Optional[str] = None,
    endpoint_url: Optional[str] = None,
    prefix: str = "assets",
    public_base_url: Optional[str] = None,
) -> S3AssetStore:
    bucket = bucket or os.environ.get("R2_BUCKET_NAME") or DEFAULT_R2_BUCKET
    endpoint = endpoint_url or os.environ.get("R2_ENDPOINT_URL") or DEFAULT_R2_ENDPOINT
    if "?" in endpoint:
        endpoint = endpoint.split("?")[0]
    access = os.environ.get("R2_ACCESS_KEY") or os.environ.get("AWS_ACCESS_KEY_ID")
    secret = os.environ.get("R2_SECRET_KEY") or os.environ.get("AWS_SECRET_ACCESS_KEY")
    public_url = public_base_url or os.environ.get("R2_PUBLIC_BASE_URL", "").strip()
    return S3AssetStore(
        bucket=bucket,
        endpoint_url=endpoint,
        prefix=prefix,
        aws_access_key_id=access,
        aws_secret_access_key=secret,
        region_name="auto",
        public_base_url=public_url or None,
    )


def r2_configured() -> bool:
    """True when minimal R2 credentials are present (bucket + endpoint + keys)."""
    bucket = (os.environ.get("R2_BUCKET_NAME") or "").strip()
    access = (os.environ.get("R2_ACCESS_KEY") or os.environ.get("AWS_ACCESS_KEY_ID") or "").strip()
    secret = (os.environ.get("R2_SECRET_KEY") or os.environ.get("AWS_SECRET_ACCESS_KEY") or "").strip()
    return bool(bucket and access and secret)


def upload_to_r2(local_path: str, key: str, content_type: Optional[str] = None) -> Optional[str]:
    """
    Upload a local file to R2. Returns public or presigned URL, or None if not configured / failed.
    """
    if not r2_configured():
        return None
    if not os.path.isfile(local_path):
        return None
    try:
        store = create_r2_store()
        ct = content_type or "video/mp4"
        if not store.upload_file(local_path, key, content_type=ct):
            return None
        return store.get_url(key)
    except Exception:
        return None


__all__ = [
    "S3AssetStore",
    "create_r2_store",
    "upload_to_r2",
    "r2_configured",
    "DEFAULT_R2_BUCKET",
    "DEFAULT_R2_ENDPOINT",
]
