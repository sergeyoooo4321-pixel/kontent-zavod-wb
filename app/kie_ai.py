"""kie.ai клиент: image generation (createTask + polling) + LLM (chat completions)."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)


class KieAIError(Exception):
    pass


class KieAITimeout(KieAIError):
    pass


class KieAIClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        http: httpx.AsyncClient,
        image_model: str = "gpt-image-2-image-to-image",
        llm_model: str = "gpt-5-2",
        poll_interval: float = 5.0,
        poll_max_attempts: int = 60,
    ):
        self._base = base_url.rstrip("/")
        self._http = http
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "cz-backend/1.0 httpx",
        }
        self._image_model = image_model
        self._llm_model = llm_model
        self._poll_interval = poll_interval
        self._poll_max_attempts = poll_max_attempts

    # ─── Image ──────────────────────────────────────────────────

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=10),
        reraise=True,
    )
    async def create_image_task(
        self,
        *,
        prompt: str,
        input_urls: list[str] | None = None,
        aspect_ratio: str = "3:4",
        resolution: str = "2K",
        model: str | None = None,
    ) -> str:
        """POST /api/v1/jobs/createTask, возвращает taskId."""
        body: dict[str, Any] = {
            "model": model or self._image_model,
            "input": {
                "prompt": prompt,
                "aspect_ratio": aspect_ratio,
                "resolution": resolution,
            },
        }
        if input_urls:
            body["input"]["input_urls"] = input_urls
        r = await self._http.post(
            f"{self._base}/api/v1/jobs/createTask",
            headers=self._headers,
            json=body,
            timeout=60.0,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 200:
            raise KieAIError(f"createTask: {data}")
        task_id = data.get("data", {}).get("taskId")
        if not task_id:
            raise KieAIError(f"createTask: no taskId in {data}")
        logger.info("kie.create_image_task taskId=%s", task_id)
        return task_id

    async def poll_image_task(self, task_id: str) -> str:
        """GET /api/v1/jobs/recordInfo, ждёт state in {success, fail}."""
        for attempt in range(self._poll_max_attempts):
            await asyncio.sleep(self._poll_interval)
            try:
                r = await self._http.get(
                    f"{self._base}/api/v1/jobs/recordInfo",
                    params={"taskId": task_id},
                    headers=self._headers,
                    timeout=30.0,
                )
                r.raise_for_status()
                data = r.json().get("data", {})
            except httpx.HTTPError as e:
                logger.warning("kie.poll attempt=%d net err: %s", attempt, e)
                continue
            state = data.get("state")
            if state == "success":
                result_json = data.get("resultJson") or "{}"
                try:
                    result = json.loads(result_json)
                except json.JSONDecodeError as e:
                    raise KieAIError(f"bad resultJson: {e}: {result_json[:200]}") from e
                urls = result.get("resultUrls") or []
                if not urls:
                    raise KieAIError(f"success but no resultUrls: {result_json[:200]}")
                logger.info("kie.poll success taskId=%s url=%s", task_id, urls[0][:80])
                return urls[0]
            if state == "fail":
                msg = data.get("failMsg") or json.dumps(data)[:300]
                raise KieAIError(f"task {task_id} failed: {msg}")
            # waiting / queuing / generating — продолжаем
        raise KieAITimeout(f"task {task_id} timeout after {self._poll_max_attempts} attempts")

    async def generate_image(
        self,
        *,
        prompt: str,
        input_urls: list[str] | None = None,
        aspect_ratio: str = "3:4",
        resolution: str = "2K",
        model: str | None = None,
    ) -> str:
        """createTask + poll → URL результата."""
        task_id = await self.create_image_task(
            prompt=prompt,
            input_urls=input_urls,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            model=model,
        )
        return await self.poll_image_task(task_id)

    # ─── LLM ────────────────────────────────────────────────────

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=10),
        reraise=True,
    )
    async def chat_json(
        self,
        *,
        system: str,
        user: str,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> dict:
        """OpenAI-совместимый chat/completions с response_format=json_object.
        Парсит content как JSON, при невалидности — один retry с подсказкой."""
        m = model or self._llm_model
        url = f"{self._base}/{m}/v1/chat/completions"

        async def _call(extra_user: str = "") -> str:
            body: dict[str, Any] = {
                "model": m,
                "response_format": {"type": "json_object"},
                "temperature": temperature,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user + extra_user},
                ],
            }
            if max_tokens:
                body["max_tokens"] = max_tokens
            r = await self._http.post(url, headers=self._headers, json=body, timeout=120.0)
            if r.status_code == 400 and "response_format" in r.text:
                # Фолбэк: модель не поддерживает response_format → убираем
                body.pop("response_format", None)
                body["messages"][0]["content"] = system + "\n\nВажно: ответ ТОЛЬКО валидный JSON, без markdown."
                r = await self._http.post(url, headers=self._headers, json=body, timeout=120.0)
            r.raise_for_status()
            data = r.json()
            content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
            return content

        content = await _call()
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # Один retry с явной подсказкой
            content = await _call(
                "\n\nВажно: предыдущий ответ был невалидным JSON. Верни ТОЛЬКО JSON-объект без markdown-обёртки."
            )
            try:
                return json.loads(content)
            except json.JSONDecodeError as e:
                raise KieAIError(f"LLM returned non-JSON twice: {content[:300]}") from e
