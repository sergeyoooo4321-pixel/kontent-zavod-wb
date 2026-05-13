from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.config import Settings


logger = logging.getLogger(__name__)


class Storage:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.media_dir = settings.MEDIA_FALLBACK_DIR
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self._s3 = None
        if settings.s3_enabled:
            self._s3 = boto3.client(
                "s3",
                endpoint_url=settings.S3_ENDPOINT,
                region_name=settings.S3_REGION,
                aws_access_key_id=settings.S3_ACCESS_KEY,
                aws_secret_access_key=settings.S3_SECRET_KEY,
            )

    def put_public(self, key: str, content: bytes, content_type: str) -> tuple[str, str]:
        safe_key = key.strip("/").replace("\\", "/")
        if self._s3:
            try:
                try:
                    self._s3.put_object(
                        Bucket=self.settings.S3_BUCKET,
                        Key=safe_key,
                        Body=content,
                        ContentType=content_type,
                        ACL="public-read",
                    )
                except ClientError as exc:
                    code = exc.response.get("Error", {}).get("Code", "")
                    if code not in {"AccessDenied", "NotImplemented", "InvalidRequest"}:
                        raise
                    self._s3.put_object(
                        Bucket=self.settings.S3_BUCKET,
                        Key=safe_key,
                        Body=content,
                        ContentType=content_type,
                    )
                base = self.settings.S3_PUBLIC_BASE.rstrip("/") or f"{self.settings.S3_ENDPOINT.rstrip('/')}/{self.settings.S3_BUCKET}"
                return f"{base}/{safe_key}", safe_key
            except (BotoCoreError, ClientError) as exc:
                logger.warning("s3.put_public fallback key=%s cause=%s", safe_key, exc)
                pass

        path = self.media_dir / safe_key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        if self.settings.MEDIA_PUBLIC_BASE:
            return f"{self.settings.MEDIA_PUBLIC_BASE.rstrip('/')}/{safe_key}", safe_key
        return path.resolve().as_uri(), safe_key


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]
