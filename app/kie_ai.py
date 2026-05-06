"""AI-провайдер: aitunnel.ru (OpenAI-совместимый агрегатор).

Класс назван KieAIClient для совместимости со всеми импортами в проекте,
но внутри ходит на api.aitunnel.ru. Поддерживает:
  • chat_json / chat_json_with_vision — через /v1/chat/completions
  • generate_image_with_retry — через /v1/images/generations
                                (gpt-image-2 поддерживает image-to-image
                                через параметр `image: [url]`)

Старые поля (poll/createTask) сохранены как deprecated — раньше у kie.ai
была async-схема createTask + recordInfo, у aitunnel всё синхронно.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class KieAIError(Exception):
    pass


class KieAITimeout(KieAIError):
    pass


def _strip_json_markdown(content: str) -> str:
    s = (content or "").strip()
    if s.startswith("```"):
        nl = s.find("\n")
        if nl > 0:
            s = s[nl + 1:]
        if s.endswith("```"):
            s = s[:-3]
        s = s.strip()
    return s


def _extract_json(content: str) -> dict | None:
    if not content:
        return None
    cleaned = _strip_json_markdown(content)
    try:
        obj = json.loads(cleaned)
        if isinstance(obj, dict):
            return obj
    except (json.JSONDecodeError, ValueError):
        pass
    start = cleaned.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(cleaned)):
        ch = cleaned[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                blob = cleaned[start:i + 1]
                try:
                    obj = json.loads(blob)
                    if isinstance(obj, dict):
                        return obj
                except (json.JSONDecodeError, ValueError):
                    return None
                break
    return None


class KieAIClient:
    """Клиент к aitunnel.ru. Имя класса историческое (раньше был kie.ai)."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        http: httpx.AsyncClient,
        image_model: str = "gpt-image-2",
        llm_model: str = "gemini-3-1-pro-preview",
        llm_fallback_model: str = "claude-sonnet-4-6",
        poll_interval: float = 5.0,
        poll_max_attempts: int = 60,
        max_concurrent: int = 6,
        rate_per_sec: float = 2.0,
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
        self._llm_fallback_model = llm_fallback_model
        self._sem = asyncio.Semaphore(max_concurrent)
        self._rate_per_sec = max(0.1, rate_per_sec)
        self._min_gap_sec = 1.0 / self._rate_per_sec
        self._last_create_at = 0.0
        self._rate_lock = asyncio.Lock()

    async def _throttle(self) -> None:
        async with self._rate_lock:
            now = time.monotonic()
            wait = self._last_create_at + self._min_gap_sec - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_create_at = time.monotonic()

    async def _request_with_429(self, method: str, url: str, **kwargs) -> httpx.Response:
        for attempt in range(5):
            r = await self._http.request(method, url, headers=self._headers, **kwargs)
            if r.status_code != 429:
                return r
            ra = 5.0
            try:
                ra = float(r.headers.get("Retry-After") or 5)
            except ValueError:
                pass
            wait = min(max(ra, 5.0 * (2 ** attempt)), 60.0)
            wait += random.uniform(0, wait * 0.2)
            logger.warning("aitunnel 429, wait=%.1fs attempt=%d/5", wait, attempt + 1)
            await asyncio.sleep(wait)
        return r

    # ─── Image generation (aitunnel /v1/images/generations) ─────────

    async def generate_image_with_retry(
        self,
        *,
        prompt: str,
        input_urls: list[str] | None = None,
        aspect_ratio: str = "3:4",
        resolution: str = "2K",  # игнорируется, оставлено для совместимости
        model: str | None = None,
        image_weight: float | None = None,  # игнорируется
        guidance_scale: float | None = None,  # игнорируется
        seed: int | None = None,
        max_retries: int = 4,
    ) -> str:
        """Сгенерировать одну картинку через aitunnel /v1/images/generations.

        gpt-image-2 поддерживает image-to-image через параметр `image: [url]`.
        size мапится из aspect_ratio: "3:4" → "1024x1536", "2:3"/"9:16" → "1024x1536",
        "1:1" → "1024x1024", "16:9"/"3:2" → "1536x1024".
        """
        size_map = {
            "3:4": "1024x1536",
            "2:3": "1024x1536",
            "9:16": "1024x1536",
            "1:1": "1024x1024",
            "16:9": "1536x1024",
            "3:2": "1536x1024",
        }
        size = size_map.get(aspect_ratio, "1024x1536")
        body: dict[str, Any] = {
            "model": model or self._image_model,
            "prompt": prompt,
            "size": size,
            "n": 1,
            "quality": "high",
        }
        if input_urls:
            clean = [u for u in input_urls if u]
            if clean:
                body["image"] = clean
        if seed is not None:
            body["seed"] = seed

        url = f"{self._base}/images/generations"
        last_err: Exception | None = None
        for attempt in range(max_retries + 1):
            await self._throttle()
            try:
                async with self._sem:
                    r = await self._request_with_429("POST", url, json=body, timeout=300.0)
            except httpx.HTTPError as e:
                last_err = e
                logger.warning("aitunnel image net err attempt %d/%d: %s",
                               attempt + 1, max_retries + 1, e)
                if attempt < max_retries:
                    await asyncio.sleep(min(2 ** attempt + random.uniform(0, 2), 30))
                continue

            if r.status_code >= 400:
                err_body = r.text[:400]
                last_err = KieAIError(f"HTTP {r.status_code}: {err_body}")
                logger.warning("aitunnel image HTTP %s attempt %d/%d: %s",
                               r.status_code, attempt + 1, max_retries + 1, err_body)
                # 5xx и transient — retry
                if r.status_code >= 500 and attempt < max_retries:
                    await asyncio.sleep(min(2 ** attempt + random.uniform(0, 2), 30))
                    continue
                # 4xx (кроме 429 уже обработан) — не retry
                if r.status_code < 500:
                    raise last_err
                continue

            try:
                data = r.json()
            except Exception as e:
                last_err = KieAIError(f"non-JSON image response: {r.text[:200]}")
                continue
            items = data.get("data") or []
            if not items:
                last_err = KieAIError(f"image: empty data: {str(data)[:300]}")
                if attempt < max_retries:
                    await asyncio.sleep(min(2 ** attempt + random.uniform(0, 2), 30))
                    continue
                break
            url_or_b64 = items[0].get("url") or items[0].get("b64_json")
            if not url_or_b64:
                last_err = KieAIError(f"image: no url/b64 in {str(items[0])[:200]}")
                continue
            # aitunnel может вернуть data:image/...;base64,... — это уже URL
            logger.info("aitunnel image ok model=%s size=%s (%d попытка)",
                        body["model"], size, attempt + 1)
            return url_or_b64

        raise KieAIError(f"generate_image failed after {max_retries + 1} attempts: {last_err}")

    # ─── Chat completions ───────────────────────────────────────────

    async def _chat_raw(
        self,
        *,
        model: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        max_attempts: int = 5,
    ) -> dict:
        """Сырой POST /v1/chat/completions, retry на 5xx/429/network."""
        url = f"{self._base}/chat/completions"
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
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
            try:
                r = await self._http.post(url, headers=self._headers, json=body, timeout=180.0)
            except (httpx.NetworkError, httpx.ReadTimeout, httpx.WriteTimeout) as e:
                wait = min(2 ** attempt, 16)
                logger.warning("chat net err attempt %d/%d: %s — wait %ds",
                               attempt + 1, max_attempts, str(e)[:120], wait)
                if attempt < max_attempts - 1:
                    await asyncio.sleep(wait)
                last_err = str(e)
                continue

            if r.status_code == 429:
                wait = float(r.headers.get("Retry-After") or min(2 ** attempt, 16))
                logger.warning("chat 429 attempt %d/%d wait=%.1fs", attempt + 1, max_attempts, wait)
                await asyncio.sleep(wait)
                continue
            if r.status_code >= 500:
                wait = min(2 ** attempt, 16)
                logger.warning("chat HTTP %s attempt %d/%d wait=%ds",
                               r.status_code, attempt + 1, max_attempts, wait)
                if attempt < max_attempts - 1:
                    await asyncio.sleep(wait)
                last_err = f"HTTP {r.status_code}: {r.text[:200]}"
                continue
            if r.status_code >= 400:
                raise KieAIError(f"HTTP {r.status_code}: {r.text[:300]}")

            return r.json()

        raise KieAIError(f"chat failed after {max_attempts} attempts: {last_err}")

    async def chat_json(
        self,
        *,
        system: str,
        user: str,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        max_attempts: int = 5,
    ) -> dict:
        """Чат с инструкцией «отвечай только валидным JSON».

        Если модель вернула не-JSON — повторяем с прогрессирующим давлением
        в инструкции. Если основная модель свалилась — фолбэк на
        llm_fallback_model.
        """
        m = model or self._llm_model
        last_content = ""
        for attempt in range(max_attempts):
            user_extra = ""
            if attempt >= 1:
                user_extra = (
                    "\n\nЖЁСТКОЕ ТРЕБОВАНИЕ: ответь ТОЛЬКО валидным JSON-объектом, "
                    "без преамбул и markdown."
                )
            sys_extra = "\n\nВажно: ответ — ТОЛЬКО валидный JSON-объект."
            messages = [
                {"role": "system", "content": system + sys_extra},
                {"role": "user", "content": user + user_extra},
            ]
            current_model = m
            try:
                data = await self._chat_raw(
                    model=current_model,
                    messages=messages,
                    temperature=temperature + 0.05 * attempt,
                    max_tokens=max_tokens,
                    max_attempts=3,
                )
            except KieAIError as e:
                if self._llm_fallback_model and self._llm_fallback_model != m:
                    logger.warning("chat_json %s fail (%s), fallback to %s",
                                   m, str(e)[:120], self._llm_fallback_model)
                    try:
                        data = await self._chat_raw(
                            model=self._llm_fallback_model,
                            messages=messages,
                            temperature=temperature + 0.05 * attempt,
                            max_tokens=max_tokens,
                            max_attempts=3,
                        )
                        current_model = self._llm_fallback_model
                    except KieAIError:
                        if attempt < max_attempts - 1:
                            await asyncio.sleep(min(2 ** attempt, 16))
                            continue
                        raise
                else:
                    raise

            choices = data.get("choices") or []
            if not choices:
                last_content = f"empty choices: {str(data)[:200]}"
                continue
            content = (choices[0].get("message") or {}).get("content") or ""
            obj = _extract_json(content)
            if obj is not None:
                return obj
            last_content = content[:200]
            logger.warning("chat_json non-JSON attempt %d/%d: %r",
                           attempt + 1, max_attempts, last_content)

        raise KieAIError(f"chat_json non-JSON after {max_attempts} attempts: {last_content}")

    async def chat_json_with_vision(
        self,
        *,
        system: str,
        user: str,
        image_url: str,
        model: str | None = None,
        max_attempts: int = 5,
    ) -> dict:
        """То же что chat_json, но с image_url в content (vision)."""
        m = model or self._llm_model
        last_content = ""
        for attempt in range(max_attempts):
            user_extra = (
                "\n\nЖЁСТКОЕ ТРЕБОВАНИЕ: ответь ТОЛЬКО валидным JSON-объектом, "
                "без преамбул и markdown."
            ) if attempt >= 1 else ""
            sys_extra = "\n\nВажно: ответ — ТОЛЬКО валидный JSON-объект."
            messages = [
                {"role": "system", "content": system + sys_extra},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user + user_extra},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                },
            ]
            try:
                data = await self._chat_raw(
                    model=m,
                    messages=messages,
                    temperature=0.2 + 0.05 * attempt,
                    max_attempts=3,
                )
            except KieAIError as e:
                if self._llm_fallback_model and self._llm_fallback_model != m:
                    logger.warning("vision %s fail (%s), fallback to %s",
                                   m, str(e)[:120], self._llm_fallback_model)
                    try:
                        data = await self._chat_raw(
                            model=self._llm_fallback_model,
                            messages=messages,
                            temperature=0.2 + 0.05 * attempt,
                            max_attempts=3,
                        )
                    except KieAIError:
                        if attempt < max_attempts - 1:
                            await asyncio.sleep(min(2 ** attempt, 16))
                            continue
                        raise
                else:
                    raise

            choices = data.get("choices") or []
            if not choices:
                last_content = "empty"
                continue
            content = (choices[0].get("message") or {}).get("content") or ""
            obj = _extract_json(content)
            if obj is not None:
                return obj
            last_content = content[:200]
            logger.warning("vision non-JSON attempt %d/%d", attempt + 1, max_attempts)

        raise KieAIError(f"vision non-JSON after {max_attempts} attempts: {last_content}")
