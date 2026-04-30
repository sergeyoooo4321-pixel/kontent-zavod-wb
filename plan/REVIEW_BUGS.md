# Найденные баги

Ревью кодовой базы `kontent-zavod-wb` (app/*.py + n8n/wf_main.json) на дату 2026-04-30.
Подсчёт: **8 критических**, **11 важных**, **9 минорных**, **5 ОК**.

---

## Критические (фиксить сразу)

### C1. Утечка `aiobotocore`-сессии при каждом `put_public` / `fetch`
**Файл:** `app/s3.py`, строки 35–69, 83–84
**Проблема:** В `__init__` создаётся `self._session = get_session()`, но клиент `s3` создаётся каждый раз заново через `_client_ctx()` → `self._session.create_client(...)`. Каждый вызов `put_public` открывает новый HTTPS-коннект (TLS-handshake + auth) и закрывает его. Для 40 фото партии это 40 коннектов. Хуже — `aclose()` закрывает только httpx-клиент, но не сам `AioSession` (у `aiobotocore.session.AioSession` нет финализации; коннекшн-пулы внутри созданных клиентов).
**Фикс:** Создать один `client` на всё время жизни сервиса:
```python
class S3Client:
    def __init__(self, ...):
        ...
        self._client = None
        self._client_cm = None

    async def start(self):
        self._client_cm = self._session.create_client("s3", ...)
        self._client = await self._client_cm.__aenter__()

    async def aclose(self):
        if self._client_cm:
            await self._client_cm.__aexit__(None, None, None)
        await self._http.aclose()

    async def put_public(self, key, data, content_type="image/jpeg"):
        await self._client.put_object(Bucket=self._bucket, Key=key, Body=data, ...)
```
И вызывать `await s3.start()` в `lifespan` сразу после создания.

---

### C2. `RunRequest` не читает `tg_file_id` из n8n payload
**Файл:** `n8n/wf_main.json` нода `wfm_prep_payload` строки 232–244, и `app/models.py` строка 16 (`ProductIn.tg_file_id`)
**Проблема:** В n8n поле задаётся как `tg_file_id: p.photo?.file_id || null`. Если фото пустое — придёт `null`. В Pydantic `tg_file_id: str` без `Optional` → `null` → ValidationError 422. Backend ответит 422, n8n из-за `onError: continueRegularOutput` молча проглотит, юзер увидит «Запускаю обработку», но партия не запустится. Ни алертов, ни logging.
**Фикс:** В `models.py` сделать валидацию строгой и в n8n проверять, что у каждого продукта реально есть file_id перед отправкой:
```python
class ProductIn(BaseModel):
    tg_file_id: str = Field(min_length=10)  # реальные file_id длинные
```
В n8n-узле `wfm_prep_payload` падать явно если у любого продукта нет `file_id`, и слать в Telegram алерт.

---

### C3. Все клиенты делят один httpx, retry на не-идемпотентном POST приведёт к дублям
**Файл:** `app/kie_ai.py` строки 55–95 (`create_image_task`), `app/ozon.py` строки 48–63, 144–150, `app/wb.py` строки 55–70, 91–93
**Проблема:** Декоратор `@retry(retry=retry_if_exception_type(httpx.HTTPError), ...)` срабатывает на **любой** `httpx.HTTPError` — включая `ReadTimeout` после того как сервер уже принял запрос. Для `POST /createTask` это второй task за деньги. Для `POST /v3/product/import` (Ozon) — два конкурирующих импорта с одним offer_id (один заглушит другой). Для `POST /content/v2/cards/upload` (WB) — две одинаковые карточки.
**Фикс:** Разделить retry-стратегию по семантике:
```python
# идемпотентные GET — retry на всё
# не-идемпотентные POST — retry только на NetworkError/ConnectError (до коннекта),
#   НЕ на ReadTimeout/HTTPStatusError/PoolTimeout
@retry(retry=retry_if_exception_type((httpx.ConnectError, httpx.ConnectTimeout)), ...)
```
Для `create_image_task` лучше добавить идемпотентный ключ, если API kie.ai его поддерживает (`Idempotency-Key` header).

---

### C4. Race на общем httpx — это **OK**, но retry-state у tenacity не thread-safe для конкурентных корутин
**Файл:** `app/telegram.py` 31–36, `app/kie_ai.py` 55, 152, `app/ozon.py` 48, `app/wb.py` 38, 55
**Проблема:** Сами декораторы `tenacity.retry` создаются один раз на метод класса. Они хранят `RetryCallState` локально для каждого вызова — это OK. Однако если две корутины одновременно вызывают `tg.send` с разными `chat_id`, и одна получит TooManyRequests 429 → tenacity не знает про rate limit (retry-if смотрит только на `httpx.NetworkError/ReadTimeout/WriteTimeout`). Это значит мы НЕ ретраим 429, но и не обрабатываем `Retry-After`. Telegram может временно лочить бота.
**Фикс:** Добавить обработку 429 в `TelegramClient.send` отдельной веткой (читать `retry_after` из json-ответа и `await asyncio.sleep(retry_after)`):
```python
if r.status_code == 429:
    ra = r.json().get("parameters", {}).get("retry_after", 1)
    await asyncio.sleep(ra)
    r = await self._http.post(...)  # один retry
```

---

### C5. `process_product_images`: блокирующий `except (S3Error, Exception)` ловит всё, включая `KeyboardInterrupt`/`SystemExit` через Exception
**Файл:** `app/pipeline.py` строки 72–76
**Проблема:** `except (S3Error, Exception)` — `Exception` уже покрывает `S3Error`, лишний; но главное — он ловит и `asyncio.CancelledError` в Python <3.8? В 3.11+ `CancelledError` наследуется от `BaseException`, не `Exception` — это OK. Но **при отмене сессии (graceful shutdown FastAPI)** `BackgroundTasks` не отменяются гарантированно; `process_product_images` запущен через `gather`, отмена не пропагируется. И самое опасное: `Exception` при ошибке `tg.send` (внутри try) → state.errors залогирован, но **`return` происходит ДО блока с pack2/pack3/extra**, оставляя 4 сгенерированные kie.ai-задачи без `await`. Деньги списались, картинки не использованы.
**Фикс:**
1. Сначала записывать `src_url` в state, потом отправлять в TG (TG-ошибка не должна валить пайплайн).
2. Заменить `except (S3Error, Exception)` на узкий `except (S3Error, TelegramError, httpx.HTTPError)`.
3. Логировать факт того, что мы прерываем пайплайн на этом товаре, в Telegram.

---

### C6. `pack_url or main` — ловушка с пустыми строками и логика картинок ломается
**Файл:** `app/pipeline.py` строки 287, 364
**Проблема:** `image_urls = [u for u in (pack_url or main, extra) if u]`. Если `pack_url == ""` (пустая строка из-за бага в kie.ai), `pack_url or main` вернёт `main` — OK. Но если `pack_url is None` и `main is None` (всё упало), `image_urls = [None or None, extra] if u` → может дать `[]`. Ozon `/v3/product/import` с пустым массивом images **отклонит весь батч** (там в items[] от других продуктов тоже не пройдут; Ozon отвергает batch atomarно если хоть один невалиден? — нет, item-level, но всё равно ошибка).
Также: для qty=1 `pack_url is None` → main, для qty=2/3 берём pack2/pack3 как главное. Но если pack2 не сгенерился, а main есть — мы не используем main как fallback для qty=2 SKU, отправляем None → теряем картинку.
**Фикс:**
```python
hero = pack_url or main  # для x2/x3 берём пакшот, иначе main
hero = hero or state.src_url  # ещё один fallback
images_for_card = [u for u in (hero, extra) if u]
if not images_for_card:
    state.errors.append(f"{sku} no images at all")
    # пропустить эту SKU из items
```

---

### C7. `state.skus_3` создаётся всегда с `weight=0, dims={l:10,w:10,h:10}` — мусор
**Файл:** `app/pipeline.py` строка 251
**Проблема:** `expand_to_3_skus({"sku": ..., "name": ..., "weight": 0, "dims": {"l": 10, "w": 10, "h": 10}})` — никогда не передаются реальные размеры/вес от пользователя. `ProductIn` их и не имеет (см. `models.py`). Но в `ARCHITECTURE.md`/HYBRID_PLAN скорее всего есть что-то про получение dims/weight из интернета (LLM-поиск). Сейчас в Ozon/WB будут уезжать карточки с весом 0 г и размерами 10×10×10 см → автоматическое отклонение по бизнес-правилам маркетплейса.
**Фикс:** Либо дополнить `ProductIn` полями `weight: int | None`, `dims: dict | None` и спрашивать в n8n, либо сделать LLM-промпт «по названию вернуть веc/габариты» и вызывать его на этапе 4. Сейчас pipeline технически работает, но коммерчески бесполезен — все карточки будут отклонены.

---

### C8. `WBClient.upload_wait` — бесконечный список без идентификации завершения
**Файл:** `app/wb.py` строки 115–125
**Проблема:** Условие выхода — «есть хоть какие-то cards в ответе». Это будет true сразу после первого `upload_status` если в БД WB уже есть старые карточки с такими `vendorCodes` (например, мы переезжаем). Возвращаем «успех» по старым данным, не дождавшись обработки текущей загрузки. И обратное: для совсем новых vendorCodes WB может вернуть пустой массив на первых 2-3 запросах — мы это правильно ждём, но возможен таймаут после 5 минут (30 × 10s).
**Фикс:** Сравнивать `updateAt` с временем вызова `upload_cards` или искать конкретный статус `processed/error` per-card. Документация WB: ответ содержит `updateAt`, нужно фильтровать `updateAt >= upload_started_at`.

---

## Важные (фиксить до E2E)

### V1. `boto3 put_object(... Body=bytes)` через aiobotocore блокирует event loop?
**Файл:** `app/s3.py` 49–69
**Проблема:** `aiobotocore` оборачивает botocore в async, **но передача больших `Body=bytes`** — данные сериализуются в момент вызова, а это синхронный код. Для 4 фото × 10 товаров × 1-2 МБ это ~40-80 МБ через event loop за раз. Не катастрофа на 2-3 секунды, но при пиковой нагрузке заметно.
**Фикс:** Если фото >5 МБ — использовать multipart upload или Stream API (`io.BytesIO`). Сейчас приемлемо, отметить TODO.

---

### V2. `RunRequest.products` `max_length=10` — синхронизировано с n8n? Да. Но обработчик 422 в n8n отсутствует
**Файл:** `app/models.py` строка 23 + `n8n/wf_main.json` нода `wfm_http_run`
**Проблема:** `min_length=1, max_length=10` соответствует HYBRID_PLAN. Но если n8n пришлёт 11 продуктов (юзер умудрился пропихнуть, либо state machine сломалась), Pydantic вернёт 422 → n8n с `onError: continueRegularOutput` проглотит. Юзер видит «🚀 Запускаю» от ноды `wfm_tg_launch`, но пайплайн не запущен.
**Фикс:** В n8n `wfm_http_run` поставить `onError: stopWorkflow` или добавить IF после HTTP-запроса: `IF: $json.queued === true` → если нет, шлём в Telegram «❌ Backend отверг batch: ...».

---

### V3. `kie.ai polling`: 5с × 60 = 5 мин на одну фотку. Последовательно main + параллельно (pack2,pack3,extra) → до 10 минут на товар
**Файл:** `app/kie_ai.py` 97–129, `app/pipeline.py` 79–113
**Проблема:** main блокирует pack-генерации (т.к. pack ждёт `state.images["main"]` для ref). При типичной генерации kie.ai 60-180 секунд это норм, но **при таймауте main** — 5 минут потерь, потом pack идут с ref=src_url (что не то, что хотели — нет того же дизайна).
**Фикс:** Запускать main и pack/extra параллельно с ref=src_url для всех (компромисс по качеству), или уменьшить poll_max_attempts с 60 до 36 (3 минуты), или сделать pack блокирующимся не на ссылке, а на `taskId` main с возможностью fallback.

---

### V4. MAX_PARALLEL_PRODUCTS=3 × 4 параллельных gen внутри = 12 одновременных kie.ai-запросов
**Файл:** `app/config.py` 46, `app/pipeline.py` 109–113
**Проблема:** Семафор `MAX_PARALLEL_PRODUCTS` ограничивает параллелизм товаров, но внутри товара 4 параллельных `generate_image`. На партии в 10 товаров, 3 в работе → 12 одновременных задач kie.ai. У kie.ai обычно rate-limit ~5-10 RPS, но и параллельных task — лимит. Получим 429, retry-strategy не настроена на rate limit.
**Фикс:** Добавить второй семафор на `KieAIClient` (`Semaphore(8)` или меньше), либо обработать 429 в `_call`/`create_image_task` с экспоненциальным backoff и `Retry-After`.

---

### V5. `_extract_list_values` молча игнорирует reference-формулы Data Validation
**Файл:** `app/excel.py` 140–155
**Проблема:** Если Ozon-шаблон содержит DV типа `=Справочники!$A$1:$A$100` (а они так и делают), `_extract_list_values` вернёт `[]`. Логирования нет. В итоге справочник **пустой**, при `pick_from_dict` без значений → `(None, True)` → атрибут не заполнен → Ozon ругается.
**Фикс:** Логировать предупреждение когда формула не inline, и реализовать парсинг ссылок (раскладывать `Sheet!$A$1:$A$100` через openpyxl: `wb[sheet][range]` → значения колонки).
```python
if "!" in f:
    sheet, rng = f.lstrip("=").split("!", 1)
    sheet = sheet.strip("'")
    ws = self._wb[sheet]
    return [str(c.value).strip() for row in ws[rng] for c in row if c.value]
```

---

### V6. `Markdown escape` в `reports.py` неполный
**Файл:** `app/reports.py` 7–9
**Проблема:** Telegram Markdown V1 требует escape только `_*`[`. V2 — больше (`~>#+-=|{}.!`). Код использует Markdown V1 (см. `parse_mode="Markdown"`), но всё равно: что если в reason от kie.ai/Ozon/WB прилетит сообщение с `]` (закрывающая квадратная)? Парсер сломается, запустится fallback в `tg.send` (строки 56-59) с retry без parse_mode — это работает, но текст приходит без форматирования. Также не экранируется `]`, что является парой к `[`, и баг особенно вероятен в reason типа `[400] Bad request`.
**Фикс:** Добавить `]` в список escape:
```python
return str(s).replace("_", r"\_").replace("*", r"\*").replace("`", r"\`").replace("[", r"\[").replace("]", r"\]")
```
Либо переехать на MarkdownV2 со всем набором.

---

### V7. `asyncio.gather(upload_ozon, upload_wb)` — нет `return_exceptions=True`
**Файл:** `app/pipeline.py` 470–473
**Проблема:** Если `upload_ozon` бросит `OzonError` (но мы её ловим внутри!) — на самом деле OK. Но если бросит `httpx.ConnectError` после исчерпания retry — она ВЫЛЕТИТ. `gather` отменит `upload_wb`, тот завершится `CancelledError`, в результате обе ноги частично отработают. Финальный отчёт не построится (выпадет в общий `except` в `run_batch`).
**Фикс:**
```python
ozon_rep, wb_rep = await asyncio.gather(
    upload_ozon(...), upload_wb(...), return_exceptions=True
)
if isinstance(ozon_rep, Exception):
    ozon_rep = Report(batch_id="", total=0, errors=[ReportItem(sku="*", mp="ozon", reason=str(ozon_rep))])
# то же для wb
```

---

### V8. `categorty_attributes` возвращает падающий `dictionary_id`, дальше последовательно тянем все values синхронно
**Файл:** `app/pipeline.py` 212–219
**Проблема:** `for a in ozon_attrs: if a.get("dictionary_id"): await deps.ozon.attribute_values(...)` — это последовательный цикл. У Ozon в категории «Продукты питания» бывает 30+ атрибутов с dictionary, каждый по 5000 значений с пагинацией. На партии в 10 товаров с 5 уникальными парами категорий = до 150 серийных запросов. Может занять 5-10 минут. Внутри уже есть retry на http-ошибки, но рабочее время пайплайна растёт.
**Фикс:** `asyncio.gather` для подгрузки values + cache на уровне процесса (категории редко меняются, можно держать в памяти 1 час):
```python
val_tasks = [deps.ozon.attribute_values(a["id"], ...) for a in ozon_attrs if a.get("dictionary_id")]
results = await asyncio.gather(*val_tasks, return_exceptions=True)
```

---

### V9. n8n state machine: переход `idle → photos` происходит при ЛЮБОМ сообщении в `idle`
**Файл:** `n8n/wf_main.json` строки 32 (jsCode), фрагмент `if (s.phase === 'idle' || trimmed === '/start' ...)`
**Проблема:** Юзер пишет «привет» → бот молча начинает фазу photos и говорит «Кидай фото». Но реальная проблема в другом: если юзер пишет «помощь» (без `ℹ️`), помощь не сработает и сразу будет фаза photos. С другой стороны, `\u{2139}\u{FE0F}` — эмодзи помощи проверяется, но если юзер на десктопе и эмодзи разный — не сработает.
**Фикс:** В idle отвечать только на чёткие триггеры (`/start`, `🚀 Новая партия`), на остальное — KB_IDLE с подсказкой. И не делать `s.phase === 'idle'` тригером старта.

---

### V10. n8n: `sd.sessions` в global static data растёт без cleanup
**Файл:** `n8n/wf_main.json` 32 (parser node)
**Проблема:** На каждый chat_id создаётся объект сессии. После завершения мы делаем `sd.sessions[chatId] = {phase: 'idle', ...}` (не `delete`), и старые `started_at`-метки накапливаются. Через год это N мегабайт в global static data n8n.
**Фикс:** Периодический cleanup или `delete sd.sessions[chatId]` после старта/сброса. Поскольку chat_id `idle` без данных — не страшно, но всё же.

---

### V11. n8n: `tg-token` хардкод в URL ноды `wfm-0-4` 
**Файл:** `n8n/wf_main.json` строка 114
**Проблема:** `https://api.telegram.org/bot8621128431:AAEuD74aEa0rowfqCFpilDg8Ma0ee9T6llI/sendMessage` — токен бота в открытом виде в JSON-файле в репо. Это утечка секрета (токен реальный или плейсхолдер? Похоже на реальный). А в `wfm_tg_launch` (строка 284) уже используется `$env.TG_BOT_TOKEN` — несогласованность.
**Фикс:** Срочно ротировать TG_BOT_TOKEN, заменить хардкод на `$env.TG_BOT_TOKEN`. Это **критическая утечка секрета**, переходит в C-секцию по факту.

---

## Минорные (на потом)

### M1. `KieAIClient.chat_json` URL: `/{model}/v1/chat/completions`
**Файл:** `app/kie_ai.py` 169–170
**Проблема:** Стандартный OpenAI-compatible URL — `/v1/chat/completions` (модель идёт в body). Здесь модель в URL — нестандартно. Если kie.ai сменит контракт, все вызовы упадут. Стоит проверить документацию kie.ai и зафиксировать в комментарии.

---

### M2. `Ozon.import_wait`: статус `processed` финальный?
**Файл:** `app/ozon.py` 162–169
**Проблема:** Условие `status in {imported, failed, processed}` — но в Ozon API статусы обычно `pending/processed/imported/failed`. `processed` — переходный после `pending`, может стать `imported` или `failed`. Завершаем рано → отчёт неточный.
**Фикс:** Использовать только `{imported, failed}`.

---

### M3. `S3Client.fetch` — нет timeout на скачку kie.ai-картинки
**Файл:** `app/s3.py` 71–75
**Проблема:** `httpx` использует таймаут из общего клиента (60 сек). Для kie.ai-CDN (часто медленный из РФ) может не хватить. Лучше задать явно.

---

### M4. Логирование URL httpx — токен Telegram в URL пути
**Файл:** `app/telegram.py` 28–29 (`{api_base}/bot{token}`)
**Проблема:** httpx по умолчанию НЕ логирует URL на INFO, только на DEBUG. Но если кто-то выкрутит LOG_LEVEL=DEBUG (в `.env` стандартно), логи будут содержать токен. Также tenacity при retry логирует ошибку с URL.
**Фикс:** Использовать httpx event hooks для маскирования или вынести `Authorization: Bearer ...` в headers вместо URL? У TG это невозможно, так задумано API. Просто запретить DEBUG в продакшене и не логировать `r.text` где может быть URL.

---

### M5. Memory: 40 фото × 2МБ = 80МБ в памяти как `bytes`
**Файл:** `app/pipeline.py` 67–85
**Проблема:** `raw = await deps.tg.get_file_bytes(...)`, потом сразу `put_public(raw)` — после этого `raw` уходит на GC. На пике одновременно держим `MAX_PARALLEL_PRODUCTS=3` × ~2МБ = 6МБ src + 4 × 1.5МБ (pack/extra) ≈ 12МБ — не страшно. **OK** на текущих параметрах, но если поднимем `MAX_PARALLEL_PRODUCTS`, надо стримить.

---

### M6. `nds_value() / 100` → 0.22, но Ozon ждёт строку «0.22» или enum «VAT_22»?
**Файл:** `app/pipeline.py` 298, `app/rules.py` 120–122
**Проблема:** Ozon API в разных версиях принимает либо строку `"0.22"`, либо enum типа `"VAT_22"`. По документации сейчас — строка `"0", "0.05", "0.07", "0.10", "0.12", "0.20"` — список фиксированный. **0.22 не в списке** → отказ! ТЗ говорит 22% — значит, в РФ ждут изменения. На сегодня 2026-04-30 ставка может уже принимать `0.22`, но возможно ещё `0.20`.
**Фикс:** Уточнить актуальные допустимые значения в Ozon API на момент запуска и сделать константу с проверкой: если 0.22 не в списке Ozon → fallback 0.20 + warning в reports.

---

### M7. `pick_from_dict` всегда возвращает best-match даже при огромной дистанции
**Файл:** `app/rules.py` 151–173
**Проблема:** Если `raw="Кофе"` и в перечне `["Чай", "Молоко", "Хлеб"]` — вернёт `"Чай"` с `was_substituted=True`. Логически — лучше вернуть None, если расстояние > порога.
**Фикс:** Добавить `max_distance` параметр с дефолтом `len(raw) // 2 + 1`. Если best > этого — вернуть `(None, True)`.

---

### M8. Нет ограничения на размер kie.ai input_urls
**Файл:** `app/kie_ai.py` 79–80
**Проблема:** Если src_url пустой/None но `input_urls=[None]` (что в pipeline возможно при race), kie.ai отклонит запрос как 400. В `process_product_images` строка 99 `[ref_url] if ref_url else None` — корректно для pack/extra, но `create_image_task` для main передаёт `[state.src_url]` без проверки.
**Фикс:** Добавить assert/raise если src_url None ДО старта main:
```python
if not state.src_url:
    state.errors.append("main: no src_url")
    return
```

---

### M9. В `_flatten_tree` пути склеиваются с `" / "` но категории Ozon/WB могут содержать `/` в имени
**Файл:** `app/pipeline.py` 141
**Проблема:** Если в Ozon есть категория `«Молоко / Йогурты»`, на дереве path станет `«Продукты / Молоко / Йогурты / ...»` — двойственность для LLM. Не критично (LLM поймёт), но в логах путаница.
**Фикс:** Использовать редкий разделитель `" › "` или `" → "`.

---

## OK (проверено, в порядке)

### OK1. `httpx.AsyncClient` один на все клиенты
**Файл:** `app/main.py` 30–58
Создаётся один `httpx.AsyncClient` в `lifespan`, шарится между TG/kie/Ozon/WB/S3.fetch. Закрывается в finally. Это правильная архитектура. Race conditions внутри httpx OK для async-режима.

### OK2. Семафор `MAX_PARALLEL_PRODUCTS` — корректное использование
**Файл:** `app/pipeline.py` 64
`async with sem:` оборачивает всю обработку товара. Правильный паттерн.

### OK3. `tenacity` retry-state — действительно thread/coroutine-safe
Каждый вызов декорированной async-функции создаёт свой `RetryCallState`. Метаданные не шарятся между корутинами.

### OK4. `lifespan` грамотно закрывает ресурсы
`finally: await http.aclose(); await s3.aclose()` — даже при exception в startup ресурсы закроются.

### OK5. `expand_to_3_skus` детерминирован, без побочек
Чистая функция от dict, тесты через `tests/` должны её покрывать. Проверка `pack_dims` корректна для qty=1/2/3.

---

## Сводка по приоритетам

| # | Категория | Кол-во |
|---|-----------|--------|
| C | Критические | 8 (+1 переходящая V11 → утечка токена) |
| V | Важные | 11 |
| M | Минорные | 9 |
| OK | Проверено | 5 |

**Самые опасные** (порядок устранения):
1. **V11** — утечка TG_BOT_TOKEN в `n8n/wf_main.json` (нужно ротировать токен **сейчас**).
2. **C7** — все карточки уйдут с весом 0 и фейковыми размерами → 100% rejection маркетплейсами.
3. **C2** — `null` в tg_file_id молча валит весь batch без алертов.
4. **C3** — retry на не-идемпотентных POST → дубли SKU и лишние списания денег за kie.ai.
5. **C1** — утечка коннектов S3 при каждом put_object.
