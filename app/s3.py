"""Yandex Object Storage клиент через aiobotocore. Per-object public-read обязателен.

Долгоживущий клиент: один s3-client на жизнь сервиса (фикс C1 — утечка TLS-коннектов).
"""
from __future__ import annotations

import asyncio
import logging

import httpx
from aiobotocore.session import AioSession, get_session
from botocore.config import Config as BotoConfig

logger = logging.getLogger(__name__)


class S3Error(Exception):
    pass


class S3Client:
    def __init__(
        self,
        *,
        endpoint: str,
        region: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        public_base: str,
        http: httpx.AsyncClient | None = None,
    ):
        self._endpoint = endpoint
        self._region = region
        self._bucket = bucket
        self._access_key = access_key
        self._secret_key = secret_key
        self._public_base = public_base.rstrip("/")
        self._session: AioSession = get_session()
        self._http = http or httpx.AsyncClient(timeout=60.0)
        self._client = None
        self._client_cm = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Создаёт долгоживущий aiobotocore-клиент. Вызывается из FastAPI lifespan."""
        async with self._lock:
            if self._client is not None:
                return
            cfg = BotoConfig(
                signature_version="s3v4",
                retries={"max_attempts": 3, "mode": "standard"},
            )
            self._client_cm = self._session.create_client(
                "s3",
                endpoint_url=self._endpoint,
                region_name=self._region,
                aws_access_key_id=self._access_key,
                aws_secret_access_key=self._secret_key,
                config=cfg,
            )
            self._client = await self._client_cm.__aenter__()
            logger.info("s3 client started endpoint=%s bucket=%s", self._endpoint, self._bucket)

    async def aclose(self) -> None:
        if self._client_cm is not None:
            try:
                await self._client_cm.__aexit__(None, None, None)
            except Exception as e:
                logger.warning("s3 client close: %s", e)
            self._client = None
            self._client_cm = None
        await self._http.aclose()

    async def put_public(
        self,
        key: str,
        data: bytes,
        content_type: str = "image/jpeg",
    ) -> str:
        """Заливает объект с ACL=public-read. Возвращает публичный URL."""
        if self._client is None:
            await self.start()
        try:
            await self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
                ACL="public-read",
            )
        except Exception as e:
            raise S3Error(f"PUT {key} failed: {e}") from e
        url = f"{self._public_base}/{key}"
        logger.info("s3.put_public key=%s size=%d", key, len(data))
        return url

    async def fetch(self, url: str, timeout: float = 60.0) -> bytes:
        """Скачивает публичный URL (для kie.ai-картинок)."""
        r = await self._http.get(url, timeout=timeout)
        r.raise_for_status()
        return r.content

    @staticmethod
    def build_key(batch_id: str, sku: str, tag: str, ext: str = "jpg") -> str:
        safe_sku = "".join(c if c.isalnum() or c in "-_" else "_" for c in sku)
        return f"{batch_id}/{safe_sku}_{tag}.{ext}"
