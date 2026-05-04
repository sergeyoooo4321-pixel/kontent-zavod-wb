"""KieLLM — минимальный async-клиент к kie.ai (OpenAI-совместимый).

Endpoint: {KIE_BASE}/{model}/v1/chat/completions
Поддерживает: tools (function calling), vision (image_url), 429-retry, biz-error retry.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class LLMError(Exception):
    pass


class _TokenBucket:
    """Простое rate-limiting: rate запросов в секунду, burst до bucket_size."""

    def __init__(self, rate: float = 4.0, bucket_size: int = 8):
        self._rate = rate
        self._tokens = float(bucket_size)
        self._size = float(bucket_size)
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            now = time.monotonic()
            self._tokens = min(self._size, self._tokens + (now - self._last) * self._rate)
            self._last = now
            if self._tokens < 1.0:
                wait = (1.0 - self._tokens) / self._rate
                await asyncio.sleep(wait)
                self._tokens = 0.0
            else:
                self._tokens -= 1.0


class KieLLM:
    def __init__(self, *, base: str, api_key: str, http: httpx.AsyncClient):
        self._base = base.rstrip("/")
        self._key = api_key
        self._http = http
        self._bucket = _TokenBucket()

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def chat(
        self,
        *,
        model: str,
        system: str | None,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        max_attempts: int = 5,
    ) -> dict:
        """Вернёт сырое сообщение ассистента (`choices[0].message`).

        messages — без system; system передаётся отдельно и автоматически
        добавляется первым элементом.
        """
        url = f"{self._base}/{model}/v1/chat/completions"
        body: dict[str, Any] = {
            "model": model,
            "messages": ([{"role": "system", "content": system}] if system else []) + messages,
            "temperature": temperature,
            "stream": False,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if max_tokens:
            body["max_tokens"] = max_tokens

        last_err = ""
        for attempt in range(max_attempts):
            await self._bucket.acquire()
            try:
                r = await self._http.post(url, headers=self._headers, json=body, timeout=180.0)
            except (httpx.NetworkError, httpx.ReadTimeout, httpx.WriteTimeout) as e:
                wait = min(2 ** attempt, 16)
                logger.warning("llm.chat net error attempt %d/%d: %s — wait %ds",
                               attempt + 1, max_attempts, str(e)[:120], wait)
                if attempt < max_attempts - 1:
                    await asyncio.sleep(wait)
                continue

            if r.status_code == 429:
                wait = float(r.headers.get("Retry-After") or min(2 ** attempt, 16))
                logger.warning("llm.chat 429 attempt %d/%d, wait=%.1fs", attempt + 1, max_attempts, wait)
                await asyncio.sleep(wait)
                continue

            if r.status_code >= 500:
                wait = min(2 ** attempt, 16)
                logger.warning("llm.chat HTTP %s attempt %d/%d, wait=%ds",
                               r.status_code, attempt + 1, max_attempts, wait)
                if attempt < max_attempts - 1:
                    await asyncio.sleep(wait)
                continue

            if r.status_code >= 400:
                raise LLMError(f"HTTP {r.status_code}: {r.text[:300]}")

            data = r.json()
            biz_code = data.get("code")
            if biz_code is not None and biz_code != 200 and "choices" not in data:
                if biz_code in (429, 500, 502, 503, 504) and attempt < max_attempts - 1:
                    wait = min(2 ** attempt, 16)
                    logger.warning("llm.chat biz %s attempt %d/%d (%s) — wait %ds",
                                   biz_code, attempt + 1, max_attempts,
                                   str(data.get("msg"))[:120], wait)
                    await asyncio.sleep(wait)
                    continue
                raise LLMError(f"biz error code={biz_code} msg={data.get('msg')!r}")

            choices = data.get("choices") or []
            if not choices:
                last_err = f"empty choices: {str(data)[:200]}"
                if attempt < max_attempts - 1:
                    await asyncio.sleep(min(2 ** attempt, 16))
                continue
            return choices[0]["message"]

        raise LLMError(f"LLM не ответил за {max_attempts} попыток: {last_err}")
