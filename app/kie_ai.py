"""kie.ai клиент: image generation (createTask + polling) + LLM (chat completions).

Ratelimit kie.ai (https://docs.kie.ai/):
  • 20 createTask за 10 секунд = 2 req/sec в среднем
  • 100+ concurrent running tasks
  • 429 → отклоняется БЕЗ постановки в очередь (не ретраит kie сама)
  • Internal Error без Retry-After — нужен наш экспоненциальный бэкофф

Реализовано:
  • Token-bucket throttle на 2 createTask/sec (KIE_RATE_PER_SEC в config)
  • Semaphore = 6 одновременных createTask
  • generate_image_with_retry: 5 попыток с паузами 5/10/20/40/60 сек + jitter
  • При 429 ждём Retry-After (если есть) или 30 сек, до 5 раз
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
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


# Модели на kie.ai с эндпоинтом /codex/v1/responses (OpenAI Responses API).
# https://docs.kie.ai/market/chat/gpt-5-4 — body имеет поле `input`, не `messages`,
# response — output[].content[].text. Нет `response_format`, поэтому JSON просим
# через инструкцию в input.
_RESPONSES_API_MODELS = {"gpt-5-4", "codex"}


def _is_responses_api(model: str) -> bool:
    """Возвращает True если модель использует /codex/v1/responses вместо chat/completions."""
    m = (model or "").lower()
    return any(m == k or m.startswith(k + "-") for k in _RESPONSES_API_MODELS)


def _strip_json_markdown(content: str) -> str:
    """Убирает markdown-обёртку ```json ... ``` если модель её добавила."""
    s = content.strip()
    if s.startswith("```"):
        # cut opening fence (```json или просто ```)
        nl = s.find("\n")
        if nl > 0:
            s = s[nl + 1:]
        # cut closing fence
        if s.endswith("```"):
            s = s[:-3]
        s = s.strip()
    return s


def _extract_json(content: str) -> dict | None:
    """Пытается извлечь JSON-объект из любого текста модели.

    Стратегии (по очереди):
      1. Чистый json.loads после strip + удаления markdown-обёртки.
      2. Поиск первого `{...}` блока с балансировкой скобок и {",[].
         Это покрывает случаи когда модель добавила «Вот ответ:» преамбулу
         или «Надеюсь, помог!» в конце.

    Возвращает dict если удалось распарсить, иначе None.
    """
    if not content:
        return None
    cleaned = _strip_json_markdown(content)
    # Стратегия 1
    try:
        obj = json.loads(cleaned)
        if isinstance(obj, dict):
            return obj
    except (json.JSONDecodeError, ValueError):
        pass
    # Стратегия 2: ищем первый {...} с балансировкой
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
    return None


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
        self._poll_interval = poll_interval
        self._poll_max_attempts = poll_max_attempts
        # Глобальный семафор concurrent createTask
        self._sem = asyncio.Semaphore(max_concurrent)
        # Token-bucket throttle для createTask: kie.ai допускает 20 / 10 сек
        self._rate_per_sec = max(0.1, rate_per_sec)
        self._min_gap_sec = 1.0 / self._rate_per_sec
        self._last_create_at = 0.0
        self._rate_lock = asyncio.Lock()

    async def _throttle_create(self) -> None:
        """Token-bucket throttle: гарантирует не более rate_per_sec createTask запросов."""
        async with self._rate_lock:
            now = time.monotonic()
            wait = self._last_create_at + self._min_gap_sec - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_create_at = time.monotonic()

    async def _request_with_429(self, method: str, url: str, **kwargs) -> httpx.Response:
        """HTTP с обработкой 429 Too Many Requests (Retry-After + бэкофф)."""
        for attempt in range(5):
            r = await self._http.request(method, url, headers=self._headers, **kwargs)
            if r.status_code != 429:
                return r
            # При 429 запрос отклонён БЕЗ очереди (kie.ai docs) — нужен наш ретрай.
            ra = 5.0
            try:
                ra = float(r.headers.get("Retry-After") or 5)
            except ValueError:
                pass
            wait = min(max(ra, 5.0 * (2 ** attempt)), 60.0)
            wait += random.uniform(0, wait * 0.2)  # jitter ±20%
            logger.warning("kie 429, retry-after=%s wait=%.1fs attempt=%d/5", ra, wait, attempt + 1)
            await asyncio.sleep(wait)
        return r

    # ─── Image ──────────────────────────────────────────────────

    @retry(
        # C3 — не-идемпотентный POST: retry только на проблемах ДО подключения
        retry=retry_if_exception_type((httpx.ConnectError, httpx.ConnectTimeout)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=5),
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
        image_weight: float | None = None,
        guidance_scale: float | None = None,
        seed: int | None = None,
    ) -> str:
        """POST /api/v1/jobs/createTask, возвращает taskId.

        Опциональные параметры image_weight / guidance_scale / seed нужны для
        edit-моделей вроде flux-kontext-pro и nano-banana-pro, где они влияют
        на консистентность товара между генерациями. Для gpt-image-2 эти поля
        обычно игнорируются API. Если не передаётся — поле в payload не идёт.
        """
        body: dict[str, Any] = {
            "model": model or self._image_model,
            "input": {
                "prompt": prompt,
                "aspect_ratio": aspect_ratio,
                "resolution": resolution,
            },
        }
        if input_urls:
            # M8 — фильтр None/empty
            clean = [u for u in input_urls if u]
            if not clean:
                raise KieAIError("createTask: input_urls all empty/None")
            body["input"]["input_urls"] = clean
        if image_weight is not None:
            body["input"]["image_weight"] = image_weight
        if guidance_scale is not None:
            body["input"]["guidance_scale"] = guidance_scale
        if seed is not None:
            body["input"]["seed"] = seed
        # Token-bucket throttle перед заходом в семафор — соблюдаем 2 req/sec
        await self._throttle_create()
        async with self._sem:
            r = await self._request_with_429(
                "POST",
                f"{self._base}/api/v1/jobs/createTask",
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
                async with self._sem:
                    r = await self._request_with_429(
                        "GET",
                        f"{self._base}/api/v1/jobs/recordInfo",
                        params={"taskId": task_id},
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
                fail_msg = data.get("failMsg") or json.dumps(data)[:300]
                logger.error("kie.poll FAIL taskId=%s msg=%s", task_id, fail_msg)
                raise KieAIError(f"task {task_id} failed: {fail_msg}")
            # waiting / queuing / generating — продолжаем
        raise KieAITimeout(f"task {task_id} timeout after {self._poll_max_attempts} attempts")

    async def create_image_task_with_retry(
        self,
        *,
        prompt: str,
        input_urls: list[str] | None = None,
        aspect_ratio: str = "3:4",
        resolution: str = "2K",
        model: str | None = None,
        image_weight: float | None = None,
        guidance_scale: float | None = None,
        seed: int | None = None,
        max_retries: int = 2,
    ) -> str:
        """create_image_task с экспоненциальным retry на сетевые ошибки И KieAIError.

        Прикрытие для редких сценариев когда kie.ai возвращает временную ошибку
        в API-ответе (code != 200) или сеть моргает — делаем 1+max_retries попыток.
        """
        last_err: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                return await self.create_image_task(
                    prompt=prompt,
                    input_urls=input_urls,
                    aspect_ratio=aspect_ratio,
                    resolution=resolution,
                    model=model,
                    image_weight=image_weight,
                    guidance_scale=guidance_scale,
                    seed=seed,
                )
            except (KieAIError, httpx.HTTPError) as e:
                last_err = e
                logger.warning(
                    "kie.create_image_task retry %d/%d: %s",
                    attempt + 1, max_retries + 1, e,
                )
                if attempt < max_retries:
                    await asyncio.sleep(2 ** attempt)
        raise KieAIError(
            f"create_image_task failed after {max_retries + 1} attempts: {last_err}"
        )

    async def generate_image_with_retry(
        self,
        *,
        prompt: str,
        input_urls: list[str] | None = None,
        aspect_ratio: str = "3:4",
        resolution: str = "2K",
        model: str | None = None,
        image_weight: float | None = None,
        guidance_scale: float | None = None,
        seed: int | None = None,
        max_retries: int = 4,
    ) -> str:
        """Create + poll с retry на ОБОИХ этапах + длинные паузы для kie-outage.

        Лимит kie.ai: 20 req / 10 сек = 2 req/sec. При Internal Error (на стороне
        kie) делаем 5 полных попыток с паузами 5/10/20/40 сек + jitter — даём
        kie восстановиться. Получается до ~75 сек ожидания на одну фотку, но
        партия из 4 фото всё равно не превышает 5 минут.
        """
        last_err: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                task_id = await self.create_image_task_with_retry(
                    prompt=prompt,
                    input_urls=input_urls,
                    aspect_ratio=aspect_ratio,
                    resolution=resolution,
                    model=model,
                    image_weight=image_weight,
                    guidance_scale=guidance_scale,
                    seed=seed,
                    max_retries=2,  # внутренний retry для самого create достаточно мал
                )
                return await self.poll_image_task(task_id)
            except (KieAIError, KieAITimeout) as e:
                last_err = e
                logger.warning(
                    "generate_image full-cycle retry %d/%d: %s",
                    attempt + 1, max_retries + 1, str(e)[:200],
                )
                if attempt < max_retries:
                    # 5, 10, 20, 40 сек + jitter ±20%
                    base = 5 * (2 ** attempt)
                    wait = base + random.uniform(0, base * 0.2)
                    await asyncio.sleep(min(wait, 60))
        raise KieAIError(
            f"generate_image failed after {max_retries + 1} attempts: {last_err}"
        )

    async def generate_image(
        self,
        *,
        prompt: str,
        input_urls: list[str] | None = None,
        aspect_ratio: str = "3:4",
        resolution: str = "2K",
        model: str | None = None,
        image_weight: float | None = None,
        guidance_scale: float | None = None,
        seed: int | None = None,
    ) -> str:
        """createTask + poll → URL результата."""
        task_id = await self.create_image_task(
            prompt=prompt,
            input_urls=input_urls,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            model=model,
            image_weight=image_weight,
            guidance_scale=guidance_scale,
            seed=seed,
        )
        return await self.poll_image_task(task_id)

    # ─── LLM ────────────────────────────────────────────────────

    async def chat_json_with_vision(
        self,
        *,
        system: str,
        user: str,
        image_url: str,
        model: str | None = None,
        temperature: float = 0.4,
        max_tokens: int | None = None,
    ) -> dict:
        """Vision chat с robust JSON extraction. Авто-выбор endpoint по модели."""
        m = model or self._llm_model
        if _is_responses_api(m):
            return await self._chat_json_responses_api(
                system=system, user=user, model=m, image_url=image_url,
            )
        url = f"{self._base}/{m}/v1/chat/completions"

        last_content = ""
        for attempt in range(5):
            use_response_format = attempt == 0
            temp = temperature + 0.1 * attempt
            user_extra = ""
            if attempt >= 1:
                user_extra = (
                    "\n\nЖЁСТКОЕ ТРЕБОВАНИЕ: ответь ТОЛЬКО валидным JSON-объектом, "
                    "без преамбул и markdown."
                )
            sys_extra = "" if use_response_format else "\n\nВажно: ответ ТОЛЬКО валидный JSON-объект, без markdown."

            body: dict[str, Any] = {
                "model": m,
                "temperature": min(temp, 1.0),
                "messages": [
                    {"role": "system", "content": system + sys_extra},
                    {"role": "user", "content": [
                        {"type": "text", "text": user + user_extra},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ]},
                ],
            }
            if use_response_format:
                body["response_format"] = {"type": "json_object"}
            if max_tokens:
                body["max_tokens"] = max_tokens

            try:
                async with self._sem:
                    r = await self._request_with_429("POST", url, json=body, timeout=180.0)
                if r.status_code == 400 and "response_format" in r.text:
                    body.pop("response_format", None)
                    async with self._sem:
                        r = await self._request_with_429("POST", url, json=body, timeout=180.0)
                r.raise_for_status()
                data = r.json()
                # kie.ai biz-error в теле при 200 OK — не ретраим
                biz_code = data.get("code")
                if biz_code is not None and biz_code != 200 and "choices" not in data:
                    raise KieAIError(
                        f"kie.ai biz error code={biz_code} msg={data.get('msg')!r} model={m!r}"
                    )
                last_content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
            except KieAIError:
                raise
            except httpx.HTTPError as e:
                logger.warning("vision attempt %d/5 HTTP error: %s", attempt + 1, str(e)[:200])
                continue

            obj = _extract_json(last_content)
            if obj is not None:
                return obj
            logger.warning("vision attempt %d/5: не удалось распарсить JSON, content=%r",
                           attempt + 1, last_content[:150])

        raise KieAIError(f"vision LLM returned non-JSON после 5 попыток: {last_content[:300]}")

    async def _chat_json_responses_api(
        self, *, system: str, user: str, model: str,
        image_url: str | None = None, max_attempts: int = 5,
    ) -> dict:
        """Responses API endpoint /codex/v1/responses (gpt-5-4 и аналоги).

        Body: {model, input:[...], reasoning:{effort}}, response — output[].content[].text.
        JSON-mode нет — просим JSON через инструкцию.
        Поддерживает vision: вместо `input_text` используется `input_image` с image_url.
        """
        url = f"{self._base}/codex/v1/responses"
        last_content = ""
        for attempt in range(max_attempts):
            user_extra = ""
            if attempt >= 1:
                user_extra = (
                    "\n\nЖЁСТКОЕ ТРЕБОВАНИЕ: ответь ТОЛЬКО валидным JSON-объектом, "
                    "без преамбул и markdown."
                )
            sys_extra = "\n\nВажно: ответ — ТОЛЬКО валидный JSON-объект."
            user_content: list[dict] = [
                {"type": "input_text", "text": user + user_extra},
            ]
            if image_url:
                user_content.append({"type": "input_image", "image_url": image_url})
            body: dict[str, Any] = {
                "model": model,
                "stream": False,
                "input": [
                    {"role": "system", "content": [{"type": "input_text", "text": system + sys_extra}]},
                    {"role": "user", "content": user_content},
                ],
                "reasoning": {"effort": "low"},
            }
            try:
                r = await self._http.post(url, headers=self._headers, json=body, timeout=180.0)
                r.raise_for_status()
                data = r.json()
                biz_code = data.get("code")
                if biz_code is not None and biz_code != 200 and "output" not in data:
                    # 5xx/429 на стороне kie — это transient (overload, прокси-флап).
                    # Ретраим с back-off в пределах max_attempts. На 4xx-ошибках
                    # клиента (400, 401, 403, 422) — сразу raise, ретрай не поможет.
                    if biz_code in (429, 500, 502, 503, 504) and attempt < max_attempts - 1:
                        wait = min(2 ** attempt, 16)
                        logger.warning(
                            "responses_api attempt %d/%d kie biz %s (%s) — retry через %ds",
                            attempt + 1, max_attempts, biz_code, data.get("msg"), wait,
                        )
                        await asyncio.sleep(wait)
                        continue
                    raise KieAIError(
                        f"kie.ai biz error code={biz_code} msg={data.get('msg')!r} model={model!r}"
                    )
                # Извлекаем text из output[].content[]
                out = data.get("output") or []
                last_content = ""
                for item in out:
                    if item.get("type") == "message":
                        for c in (item.get("content") or []):
                            if c.get("type") in ("output_text", "text"):
                                last_content += c.get("text") or ""
            except KieAIError:
                raise
            except httpx.HTTPError as e:
                logger.warning("responses_api attempt %d/%d HTTP error: %s",
                               attempt + 1, max_attempts, str(e)[:200])
                if attempt < max_attempts - 1:
                    await asyncio.sleep(min(2 ** attempt, 16))
                continue
            obj = _extract_json(last_content)
            if obj is not None:
                return obj
            logger.warning("responses_api attempt %d/%d: non-JSON content=%r",
                           attempt + 1, max_attempts, last_content[:150])
        raise KieAIError(f"LLM (Responses API) non-JSON после {max_attempts} попыток (model={model}): {last_content[:300]}")

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
        """LLM с robust JSON extraction. Авто-выбор endpoint:
          • gpt-5-4 / codex-* → /codex/v1/responses (Responses API)
          • прочие (gpt-5-2, gemini-*, …) → /{model}/v1/chat/completions
        """
        m = model or self._llm_model
        if _is_responses_api(m):
            return await self._chat_json_responses_api(system=system, user=user, model=m)
        url = f"{self._base}/{m}/v1/chat/completions"

        last_content = ""
        for attempt in range(5):
            # Прогрессирующая стратегия от попытки к попытке
            use_response_format = attempt == 0  # на 1-й пробуем с native JSON-mode
            temp = temperature + 0.1 * attempt
            user_extra = ""
            if attempt >= 1:
                user_extra = (
                    "\n\nЖЁСТКОЕ ТРЕБОВАНИЕ: ответь ТОЛЬКО валидным JSON-объектом. "
                    "Никаких преамбул, пояснений или markdown-обёрток. "
                    "Сразу с открывающей фигурной скобки `{` до закрывающей `}`."
                )
            sys_extra = ""
            if not use_response_format:
                sys_extra = "\n\nВажно: ответ ТОЛЬКО валидный JSON-объект, без markdown."

            body: dict[str, Any] = {
                "model": m,
                "temperature": min(temp, 1.0),
                "messages": [
                    {"role": "system", "content": system + sys_extra},
                    {"role": "user", "content": user + user_extra},
                ],
            }
            if use_response_format:
                body["response_format"] = {"type": "json_object"}
            if max_tokens:
                body["max_tokens"] = max_tokens

            try:
                r = await self._http.post(url, headers=self._headers, json=body, timeout=120.0)
                # Если 400 на response_format — повторяем без него на этой же попытке
                if r.status_code == 400 and "response_format" in r.text:
                    body.pop("response_format", None)
                    body["messages"][0]["content"] = system + sys_extra + \
                        "\n\nВажно: ответ ТОЛЬКО валидный JSON, без markdown."
                    r = await self._http.post(url, headers=self._headers, json=body, timeout=120.0)
                r.raise_for_status()
                data = r.json()
                # kie.ai иногда возвращает HTTP 200 с business-error в теле:
                # {"code":422,"msg":"The model is not supported","data":null}.
                # Это значит модель не существует / нет доступа — нет смысла ретраить.
                biz_code = data.get("code")
                if biz_code is not None and biz_code != 200 and "choices" not in data:
                    raise KieAIError(
                        f"kie.ai biz error code={biz_code} msg={data.get('msg')!r} model={m!r}"
                    )
                last_content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
            except KieAIError:
                # Бизнес-ошибка от kie (модель не поддерживается и т.п.) — не ретраим
                raise
            except httpx.HTTPError as e:
                logger.warning("chat_json attempt %d/5 HTTP error: %s", attempt + 1, str(e)[:200])
                last_content = ""
                continue

            obj = _extract_json(last_content)
            if obj is not None:
                return obj
            logger.warning(
                "chat_json attempt %d/5: не удалось распарсить JSON, content=%r",
                attempt + 1, last_content[:150],
            )

        raise KieAIError(f"LLM returned non-JSON после 5 попыток (model={m}): {last_content[:300]}")
