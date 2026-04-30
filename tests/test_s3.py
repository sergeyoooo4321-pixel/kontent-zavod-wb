"""Интеграционные тесты Yandex S3 (с реальным бакетом).

Активируется флагом `--integration` и требует env:
    S3_ACCESS_KEY, S3_SECRET_KEY, S3_BUCKET, S3_ENDPOINT, S3_PUBLIC_BASE
"""
import os

import httpx
import pytest


@pytest.mark.asyncio
async def test_s3_put_and_anon_get(integration):
    if not integration:
        pytest.skip("requires --integration")
    from app.s3 import S3Client

    s3 = S3Client(
        endpoint=os.environ["S3_ENDPOINT"],
        region=os.environ.get("S3_REGION", "ru-central1"),
        bucket=os.environ["S3_BUCKET"],
        access_key=os.environ["S3_ACCESS_KEY"],
        secret_key=os.environ["S3_SECRET_KEY"],
        public_base=os.environ["S3_PUBLIC_BASE"],
    )
    key = "_smoke/test.txt"
    payload = b"smoke-from-pytest"
    url = await s3.put_public(key, payload, content_type="text/plain")
    async with httpx.AsyncClient() as http:
        r = await http.get(url)
        assert r.status_code == 200
        assert r.content == payload
    await s3.aclose()
