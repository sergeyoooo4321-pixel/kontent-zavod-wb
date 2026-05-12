"""Тесты retry-обёрток kie.ai (create_image_task_with_retry).

DEPRECATED: после миграции на aitunnel.ru job-style API удалён. Тесты skip.
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.kie_ai import KieAIClient, KieAIError

pytestmark = pytest.mark.skip(reason="kie.ai async API заменён на aitunnel — тесты не релевантны")


@pytest.mark.asyncio
async def test_create_image_task_with_retry_succeeds_on_third_attempt(respx_mock):
    """Первые 2 попытки kie возвращает ошибку, на 3-й — taskId."""
    route = respx_mock.post("https://api.kie.ai/api/v1/jobs/createTask").mock(
        side_effect=[
            httpx.Response(500, json={"code": 500, "message": "internal"}),
            httpx.Response(500, json={"code": 500, "message": "internal"}),
            httpx.Response(200, json={"code": 200, "data": {"taskId": "tsk-3"}}),
        ]
    )
    async with httpx.AsyncClient() as http:
        client = KieAIClient(
            base_url="https://api.kie.ai",
            api_key="k",
            http=http,
            poll_interval=0.001,
            poll_max_attempts=1,
        )
        tid = await client.create_image_task_with_retry(
            prompt="hi",
            input_urls=["https://x/img.jpg"],
            max_retries=2,
        )
        assert tid == "tsk-3"
        assert route.call_count == 3


@pytest.mark.asyncio
async def test_create_image_task_with_retry_gives_up(respx_mock):
    """3 фейла подряд → KieAIError с понятным message."""
    respx_mock.post("https://api.kie.ai/api/v1/jobs/createTask").mock(
        return_value=httpx.Response(500, json={"code": 500, "message": "internal"})
    )
    async with httpx.AsyncClient() as http:
        client = KieAIClient(
            base_url="https://api.kie.ai",
            api_key="k",
            http=http,
            poll_interval=0.001,
            poll_max_attempts=1,
        )
        with pytest.raises(KieAIError) as exc:
            await client.create_image_task_with_retry(
                prompt="hi",
                input_urls=["https://x/img.jpg"],
                max_retries=2,
            )
        assert "after 3 attempts" in str(exc.value)


@pytest.mark.asyncio
async def test_create_image_task_with_retry_passes_extra_params(respx_mock):
    """image_weight, guidance_scale, seed уходят в payload."""
    route = respx_mock.post("https://api.kie.ai/api/v1/jobs/createTask").mock(
        return_value=httpx.Response(200, json={"code": 200, "data": {"taskId": "tsk-x"}})
    )
    async with httpx.AsyncClient() as http:
        client = KieAIClient(
            base_url="https://api.kie.ai",
            api_key="k",
            http=http,
            poll_interval=0.001,
            poll_max_attempts=1,
        )
        await client.create_image_task_with_retry(
            prompt="hi",
            input_urls=["https://x/img.jpg"],
            image_weight=0.85,
            guidance_scale=7.5,
            seed=42,
        )
        sent_body = json.loads(route.calls[0].request.content)
        assert sent_body["input"]["image_weight"] == 0.85
        assert sent_body["input"]["guidance_scale"] == 7.5
        assert sent_body["input"]["seed"] == 42
