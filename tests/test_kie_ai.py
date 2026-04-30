"""Тесты kie.ai клиента (с моками httpx)."""
import json

import httpx
import pytest

from app.kie_ai import KieAIClient, KieAIError, KieAITimeout


@pytest.mark.asyncio
async def test_create_image_task(respx_mock):
    from respx import MockRouter
    mr: MockRouter = respx_mock
    mr.post("https://api.kie.ai/api/v1/jobs/createTask").mock(
        return_value=httpx.Response(200, json={"code": 200, "data": {"taskId": "tsk-1"}})
    )
    async with httpx.AsyncClient() as http:
        client = KieAIClient(base_url="https://api.kie.ai", api_key="k", http=http,
                             poll_interval=0.01, poll_max_attempts=3)
        tid = await client.create_image_task(prompt="hi", input_urls=["https://x/img.jpg"])
        assert tid == "tsk-1"


@pytest.mark.asyncio
async def test_poll_image_success(respx_mock):
    mr = respx_mock
    responses = [
        httpx.Response(200, json={"data": {"state": "generating"}}),
        httpx.Response(200, json={"data": {"state": "generating"}}),
        httpx.Response(200, json={
            "data": {"state": "success", "resultJson": json.dumps({"resultUrls": ["https://kie/x.png"]})}
        }),
    ]
    mr.get("https://api.kie.ai/api/v1/jobs/recordInfo").mock(side_effect=responses)
    async with httpx.AsyncClient() as http:
        client = KieAIClient(base_url="https://api.kie.ai", api_key="k", http=http,
                             poll_interval=0.01, poll_max_attempts=10)
        url = await client.poll_image_task("tsk-1")
        assert url == "https://kie/x.png"


@pytest.mark.asyncio
async def test_poll_image_fail(respx_mock):
    respx_mock.get("https://api.kie.ai/api/v1/jobs/recordInfo").mock(
        return_value=httpx.Response(200, json={"data": {"state": "fail", "failMsg": "bad input"}})
    )
    async with httpx.AsyncClient() as http:
        client = KieAIClient(base_url="https://api.kie.ai", api_key="k", http=http,
                             poll_interval=0.01, poll_max_attempts=3)
        with pytest.raises(KieAIError):
            await client.poll_image_task("tsk-1")


@pytest.mark.asyncio
async def test_poll_image_timeout(respx_mock):
    respx_mock.get("https://api.kie.ai/api/v1/jobs/recordInfo").mock(
        return_value=httpx.Response(200, json={"data": {"state": "generating"}})
    )
    async with httpx.AsyncClient() as http:
        client = KieAIClient(base_url="https://api.kie.ai", api_key="k", http=http,
                             poll_interval=0.001, poll_max_attempts=3)
        with pytest.raises(KieAITimeout):
            await client.poll_image_task("tsk-1")


@pytest.mark.asyncio
async def test_chat_json_ok(respx_mock):
    respx_mock.post("https://api.kie.ai/gpt-5-2/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={
            "choices": [{"message": {"content": '{"ok": true, "lang": "ru"}'}}]
        })
    )
    async with httpx.AsyncClient() as http:
        client = KieAIClient(base_url="https://api.kie.ai", api_key="k", http=http)
        out = await client.chat_json(system="s", user="u")
        assert out == {"ok": True, "lang": "ru"}
