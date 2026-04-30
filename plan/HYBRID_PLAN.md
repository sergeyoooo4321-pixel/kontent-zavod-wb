# HYBRID_PLAN — гибридная архитектура «Контент завод»

**Версия:** 1.0
**Дата:** 2026-04-30
**Источники:**
- ТЗ v1.4 (`ТЗ_Агент_1_рабочий_сценарий.md`)
- План n8n-only v1.0 (`plan/workflow_plan.md`)
- План интеграции kie.ai + Yandex S3 v1.0 (`plan/integration_plan.md`)
- Текущие n8n-модули: `modules/wf_main_v8.json`, `modules/sub_wf_1_v9.json`, `modules/sub_wf_2.json`, `modules/sub_wf_3.json`

**Решение архитектуры:**
n8n остаётся только как «тонкий Telegram-фронт» (state machine для сбора партии).
Всё heavy lifting (kie.ai polling, S3, шаблоны Ozon, Ozon/WB API, отчёты, асинхронная оркестрация) переезжает в Python-бэкенд (FastAPI + asyncio).
Это убирает 81-нодовое нагромождение, бесконечные `Wait/Set/IF` poll-циклы, ограничение 5 минут на execution и хрупкие Code-ноды.

---

## А. Архитектура

### А.1 Высокоуровневая диаграмма

```
┌──────────────┐      ┌──────────────────────────────┐      ┌───────────────────────────┐
│  Telegram    │      │  n8n (на 2.26.65.241)        │      │  Python FastAPI           │
│  пользователь│─────►│  WF_MAIN                     │─────►│  cz-backend.service       │
│              │      │  • TG Trigger                │POST  │  127.0.0.1:8000           │
│              │      │  • State-machine парсер      │ /api │  • /api/run (async)       │
│              │      │  • Telegram реплаи (idle/    │ /run │  • /healthz               │
│              │      │    photos/names/confirm)     │      │                           │
│              │      │  • HTTP Request → Python     │      │  pipeline.py (asyncio)    │
│              │      │  • Возврат «🚀 Запускаю...»  │      │  ┌──────────────────────┐ │
└──────────────┘      └──────────────────────────────┘      │  │ per-product gather() │ │
       ▲                              ▲                     │  │  src→S3→main→pack2/3 │ │
       │                              │                     │  │  /extra → S3         │ │
       │  promo/прогресс/отчёт         │                     │  │  → category(LLM)     │ │
       │  (sendMessage напрямую        │                     │  │  → templates(XLSX)   │ │
       │   из Python)                  │                     │  │  → fill+upload       │ │
       └───────────────────────────────┴─────────────────────┤  │  → report(MD)        │ │
                                                             │  └──────────────────────┘ │
                                                             └─┬─────────────────────────┘
                                                               │
                                ┌──────────────────────────────┼──────────────┐
                                ▼                              ▼              ▼
                       ┌─────────────────┐           ┌──────────────┐  ┌──────────────┐
                       │ kie.ai          │           │ Yandex S3    │  │ Ozon Seller  │
                       │ • image-2-image │           │ public-read  │  │ + WB Content │
                       │ • gpt-5-2 LLM   │           │ ru-central1  │  │   API        │
                       └─────────────────┘           └──────────────┘  └──────────────┘
```

### А.2 Что остаётся в n8n (минимум)

| Узел в n8n | Назначение |
|---|---|
| Telegram Trigger | Точка входа: ловит сообщения от пользователя |
| Code «накопить и распарсить вход» | State-machine: `idle → photos → names → confirm → running`. Хранит сессию в `$getWorkflowStaticData('global').sessions[chatId]` |
| Telegram реплаи | UX-сообщения для каждого состояния («пришли фото», «теперь наименования», «подтверди?», «запускаю») |
| HTTP Request → Python | В состоянии `running`: `POST http://host.docker.internal:8000/api/run` с собранным `batch` |
| Error Trigger + Telegram «критическая ошибка» | Только для отлова падений n8n самого (не Python) |

**Удаляются:** все три `Execute Workflow: SUB_WF_*`, статусные ноды `«фото готовы»` / `«шаблоны готовы»`, нода «финальный отчёт MD», `Telegram: финальный отчёт`. Сами `SUB_WF_1/2/3` деактивируются (можно оставить в БД n8n для архива, но `active=false`).

### А.3 Что в Python

Всё остальное:
- скачивание исходного фото из Telegram (`getFile` + downloadFile);
- заливка в Yandex S3 (boto3, public-read);
- async-вызовы kie.ai с polling;
- генерация 4 фото (image-to-image, main → pack2/3/extra параллельно через `asyncio.gather`);
- LLM-подбор категории Ozon/WB через kie.ai gpt-5-2;
- скачивание XLSX-шаблонов Ozon, парсинг Data Validation (openpyxl);
- заполнение шаблонов по правилам §5.2 ТЗ (расширение до 3 SKU, габариты, веса, лимиты, мультивыбор `;`);
- POST в Ozon `/v3/product/import` + polling status;
- POST в WB `/content/v2/cards/upload` + polling list;
- Markdown-отчёт;
- промежуточные пинги в Telegram через bot token напрямую (никаких callback'ов в n8n).

### А.4 Почему такой выбор

1. **Polling** kie.ai даёт 4 цикла «createTask → wait 5s → recordInfo → IF success/fail → loop» × 4 фото × 10 товаров = 160+ нод и риск упереться в timeouts execution. В Python — `await asyncio.sleep(5)` в обычной корутине.
2. **Параллелизм по товарам** — `asyncio.gather()` + `asyncio.Semaphore(N)` для rate-limit kie.ai. В n8n параллелизм только через `splitInBatches` + `Promise.all` в Code, что хрупко.
3. **XLSX с Data Validation** — стандартная нода `spreadsheetFile` НЕ читает Data Validation. В Python `openpyxl.worksheet.data_validations` читает.
4. **Универсальные ретраи** — в Python через декораторы (`tenacity`), а не дублирование IF/Wait/Set в n8n.
5. **Тестируемость** — pytest с моками для каждого модуля.
6. **Разделение ответственности** — UX (n8n) ≠ бизнес-логика (Python). Можно поменять фронт (например, добавить Web UI) не трогая бэк.

---

## Б. Структура репо `kontent-zavod-wb/`

```
kontent-zavod-wb/
├── README.md                      # обзор проекта, юзер-сценарий
├── ARCHITECTURE.md                # схема, разделение n8n vs Python, контракты
├── DEPLOY.md                      # как развернуть с нуля (требования, шаги, env, systemd)
├── .env.example                   # пример с placeholder'ами
├── .gitignore                     # python+ide+secrets+snapshot_server
├── requirements.txt               # fastapi, uvicorn, httpx, boto3, openpyxl, pydantic-settings, tenacity, pytest, pytest-asyncio, python-multipart, aiofiles, pillow
├── pyproject.toml                 # опц., если будет poetry/ruff конфиг
├── app/
│   ├── __init__.py
│   ├── main.py                    # FastAPI app, эндпоинты, lifespan
│   ├── config.py                  # pydantic-settings Settings
│   ├── models.py                  # Pydantic-схемы Batch/Product/Report/RunRequest
│   ├── telegram.py                # TelegramClient (sendMessage, getFile, downloadFile)
│   ├── kie_ai.py                  # KieAIClient (createTask, recordInfo polling, chat completions)
│   ├── s3.py                      # S3Client (boto3-aiobotocore: put_object public-read, get_url)
│   ├── prompts.py                 # builders для main/pack2/pack3/extra + промпты для LLM
│   ├── rules.py                   # бизнес-правила §5.2: addCm, packDims, roundToHundred, stripBrand, limit60, limit100, ndsValue
│   ├── excel.py                   # OzonTemplate (download, parse data-validations, fill, save)
│   ├── ozon.py                    # OzonClient (category tree, attribute, attribute/values, product/import + polling)
│   ├── wb.py                      # WBClient (object/parent/all, charcs, directory, cards/upload + polling)
│   ├── reports.py                 # build_report_md(report) → str
│   └── pipeline.py                # run_batch(batch) — оркестратор
├── n8n/
│   ├── wf_main.json               # урезанный WF_MAIN (только парсер + HTTP вызов)
│   └── README.md                  # как импортировать, какие env нужны в n8n-контейнере
├── tests/
│   ├── __init__.py
│   ├── conftest.py                # фикстуры, async event loop
│   ├── test_kie_ai.py             # моки httpx, проверка polling до success/fail
│   ├── test_s3.py                 # реальный S3 (по флагу --integration), put + anon get
│   ├── test_rules.py              # юнит-тесты §5.2 (3 SKU, габариты, веса, мультивыбор)
│   ├── test_excel.py              # парсинг тестового XLSX с data validation
│   ├── test_telegram.py           # моки sendMessage/getFile
│   ├── test_pipeline.py           # пайплайн на моках всех клиентов
│   └── test_e2e.py                # full-run на моках (default) или реальный (--live)
└── scripts/
    ├── deploy_systemd.sh          # idempotent: создаёт юзера, /home/albert/cz-backend, копирует unit
    ├── cz-backend.service         # systemd unit-файл
    └── smoke_test.py              # standalone CLI: дёргает /api/run на тестовом batch без Telegram
```

---

## В. Контракт между n8n и Python

### В.1 Запрос: `POST http://host.docker.internal:8000/api/run`

Из контейнера n8n в Docker до хост-Python: проверить два варианта при деплое:
- `http://host.docker.internal:8000` — работает на recent Docker (linux добавили в 20.10+).
- `http://172.17.0.1:8000` — IP моста `docker0` (классический fallback на Ubuntu).

Передать оба в env n8n как `CZ_BACKEND_URL`, использовать тот, что отвечает на `/healthz`.

```jsonc
{
  "batch_id": "uuid-string",            // генерит n8n при переходе в running
  "chat_id": 123456789,                 // целое, Telegram chat id
  "products": [
    {
      "idx": 0,                         // порядковый номер
      "sku": "ABC-001",                 // артикул (валидирован парсером)
      "name": "Кофе зерновой Арабика",  // наименование
      "tg_file_id": "AgACAgIAA..."      // file_id от Telegram (биггест photo)
    }
    // до 10 элементов
  ]
}
```

### В.2 Ответ Python: 202 Accepted, мгновенно

```jsonc
{
  "batch_id": "uuid-string",
  "queued": true,
  "received_at": "2026-04-30T10:00:00Z"
}
```

Внутри `main.py` на хендлере `/api/run`:
```
async def run(req: RunRequest, bg: BackgroundTasks):
    bg.add_task(pipeline.run_batch, req)
    return {"batch_id": req.batch_id, "queued": True, "received_at": now()}
```

(Альтернатива — `asyncio.create_task(pipeline.run_batch(req))`. BackgroundTasks предпочтительнее: FastAPI сам уберёт ссылку после завершения, и есть лог.)

### В.3 Никаких callback'ов обратно в n8n

Python шлёт сообщения юзеру **сам**, через bot token (env `TG_BOT_TOKEN`):
- `🟦 Запускаю обработку партии {batch_id} (10 товаров)`
- `📸 Сгенерированы фото для {sku} (4/4)`
- `📂 Шаблоны Ozon скачаны (3 категории)`
- `📤 Карточки залиты на Ozon: 8/10, на WB: 9/10`
- финальный markdown-отчёт.

Один раз в `pipeline.run_batch()` ловится `Exception` → отдельный `await tg.send(chat_id, "❌ Критическая ошибка: ...")`.

---

## Г. Содержимое каждого Python-модуля

> Псевдокод/сигнатуры, не готовый Python.

### Г.1 `app/config.py`

```
class Settings(pydantic_settings.BaseSettings):
    # Telegram
    TG_BOT_TOKEN: str
    TG_API_BASE: str = "https://api.telegram.org"
    # kie.ai
    KIE_BASE: str = "https://api.kie.ai"
    KIE_API_KEY: str                    # bearer
    KIE_IMAGE_MODEL: str = "gpt-image-2-image-to-image"
    KIE_LLM_MODEL: str = "gpt-5-2"
    KIE_POLL_INTERVAL_SEC: float = 5.0
    KIE_POLL_MAX_ATTEMPTS: int = 60     # 5 минут
    # S3 Yandex
    S3_ENDPOINT: str = "https://storage.yandexcloud.net"
    S3_REGION: str = "ru-central1"
    S3_BUCKET: str = "cz-content-zavod-prod"
    S3_ACCESS_KEY: str
    S3_SECRET_KEY: str
    S3_PUBLIC_BASE: str = "https://storage.yandexcloud.net/cz-content-zavod-prod"
    # Маркетплейсы
    OZON_BASE: str = "https://api-seller.ozon.ru"
    OZON_CLIENT_ID: str | None = None
    OZON_API_KEY: str | None = None
    WB_BASE: str = "https://content-api.wildberries.ru"
    WB_TOKEN: str | None = None
    # Параллелизм
    MAX_PARALLEL_PRODUCTS: int = 3      # asyncio.Semaphore — чтобы не упереться в rate-limit kie.ai
    HTTP_TIMEOUT_SEC: int = 60
    # LLM
    LLM_TEMPERATURE: float = 0.2
    # Логирование
    LOG_LEVEL: str = "INFO"
    class Config: env_file = ".env"

settings = Settings()
```

### Г.2 `app/models.py`

```
class ProductIn(BaseModel):
    idx: int
    sku: str
    name: str
    tg_file_id: str

class RunRequest(BaseModel):
    batch_id: str
    chat_id: int
    products: list[ProductIn]   # 1..10

class ProductState(BaseModel):
    # внутреннее состояние пайплайна
    idx: int
    sku: str
    name: str
    tg_file_id: str
    src_url: str | None = None
    images: dict[str, str] = {}      # {"main": url, "pack2": url, ...}
    ozon_category: dict | None = None
    wb_subject: dict | None = None
    skus_3: list[dict] = []          # после расширения до 3 SKU
    ozon_status: dict = {}           # {sku_x1: "imported", sku_x2: "failed", ...}
    wb_status: dict = {}
    errors: list[str] = []

class Report(BaseModel):
    batch_id: str
    total: int
    successes: list[dict]
    errors: list[dict]
    warnings: list[dict]
```

### Г.3 `app/telegram.py`

```
class TelegramClient:
    def __init__(self, token: str, http: httpx.AsyncClient): ...
    async def send(self, chat_id: int, text: str, parse_mode: str = "Markdown") -> None
    async def get_file_path(self, file_id: str) -> str       # GET /bot{tok}/getFile
    async def download_file(self, file_path: str) -> bytes   # GET /file/bot{tok}/{path}
    # с tenacity: 3 retries on httpx.ReadTimeout, NetworkError
```

Исключения: `TelegramError(Exception)`. Env: `TG_BOT_TOKEN`, `TG_API_BASE`.

### Г.4 `app/kie_ai.py`

```
class KieAITimeout(Exception): ...
class KieAIError(Exception): ...

class KieAIClient:
    def __init__(self, base_url, api_key, http): ...

    # IMAGE
    async def create_image_task(self, *, model: str, prompt: str,
                                input_urls: list[str] | None = None,
                                aspect_ratio: str = "3:4",
                                resolution: str = "2K") -> str
        # POST /api/v1/jobs/createTask, return taskId

    async def poll_image_task(self, task_id: str,
                              interval: float = 5.0,
                              max_attempts: int = 60) -> str
        # GET /api/v1/jobs/recordInfo?taskId=...
        # state in {waiting, queuing, generating, success, fail}
        # возвращает первый url из resultJson.resultUrls
        # raise KieAITimeout если max_attempts исчерпан
        # raise KieAIError если state == fail

    async def generate_image(self, **kwargs) -> str
        # createTask + poll, возвращает image url

    # LLM
    async def chat_json(self, *, system: str, user: str,
                        model: str | None = None,
                        json_schema_hint: dict | None = None) -> dict
        # POST /gpt-5-2/v1/chat/completions
        # response_format={"type":"json_object"}
        # парсит choices[0].message.content как JSON, retry если невалидно

    # async with KieAIClient(...) — закрытие httpx сессии
```

Retry: `@tenacity.retry(retry_if_exception_type(httpx.HTTPError), stop_after_attempt(3), wait_exponential(1, 10))`.
Env: `KIE_BASE`, `KIE_API_KEY`, `KIE_IMAGE_MODEL`, `KIE_LLM_MODEL`, `KIE_POLL_INTERVAL_SEC`, `KIE_POLL_MAX_ATTEMPTS`.

### Г.5 `app/s3.py`

```
class S3Client:
    def __init__(self, endpoint, region, bucket, access_key, secret_key): ...
    async def put_public(self, key: str, data: bytes,
                         content_type: str = "image/jpeg") -> str
        # boto3 / aiobotocore: put_object с ACL=public-read
        # возвращает public URL (path-style)

    async def fetch(self, url: str) -> bytes
        # для скачивания результата kie.ai (через httpx, но в этом же модуле для удобства)

    @staticmethod
    def build_key(batch_id: str, sku: str, tag: str, ext: str = "jpg") -> str
        # f"{batch_id}/{sku}_{tag}.{ext}"
```

Per-object ACL `public-read` обязателен (бакет может быть `public-read`, но Yandex иногда требует и per-object).
Env: `S3_ENDPOINT`, `S3_REGION`, `S3_BUCKET`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_PUBLIC_BASE`.

### Г.6 `app/prompts.py`

```
def build_main_prompt(product_name: str, brand: str | None) -> str
def build_pack_prompt(product_name: str, qty: int) -> str       # qty in {2, 3}
def build_extra_prompt(product_name: str) -> str
def build_category_prompt(product_name: str,
                          ozon_leaves: list[dict],
                          wb_leaves: list[dict]) -> tuple[str, str]   # (system, user)
def build_titles_prompt(product_name: str, brand: str | None,
                        category_path: str, qty: int) -> tuple[str, str]
def build_annotation_prompt(...) -> tuple[str, str]
```

В `prompts.py` хардкодим базовые требования ТЗ §3.3 (3:4, RU, без искажений упаковки, единый стиль).

### Г.7 `app/rules.py`

```
def add_cm_to_dims(dims: dict, cm: int = 1) -> dict
def round_up(x: float) -> int
def round_to_hundred(g: int) -> int                     # 97 → 100
def round_up_2dp(x: float) -> float                     # для веса WB
def pack_dims(unit_dims: dict, qty: int) -> dict        # меньшая сторона × qty
def expand_to_3_skus(product: dict) -> list[dict]       # x1, x2, x3 со всеми пересчётами
def strip_brand(name: str, brand: str | None) -> str
def limit_chars(s: str, n: int) -> str                  # с многоточием при необходимости
def join_multivalue(values: list[str]) -> str           # через ;
def pick_from_dict(dict_values: list[str], raw: str,
                   strategy: str = "exact|nearest") -> tuple[str, bool]
                                                        # (value, was_substituted)
def nds_value() -> int                                  # 22
```

Тестируется в `tests/test_rules.py` — это формальные правила §5.2.

### Г.8 `app/excel.py`

```
class OzonTemplate:
    @classmethod
    async def download_for_category(cls, ozon: "OzonClient",
                                    category_id: int,
                                    type_id: int) -> "OzonTemplate"
        # Ozon /v1/description-category/template или fallback на pure API
        # сохраняет в tmpfile, открывает openpyxl

    def read_dictionaries(self) -> dict[str, list[str]]
        # Парсит Data Validation: ws.data_validations.dataValidation
        # Также парсит лист "Справочники" если есть
        # Возвращает {column_header: [allowed_values]}

    def fill_rows(self, rows: list[dict[str, str]]) -> None
        # rows — заполненные значения по колонкам;
        # multivalue склеивается через ;
        # фото — только URL, никаких embedded image

    def save(self, path: str) -> None
```

Используются: `openpyxl` (read+write+data_validations), `aiofiles` для tmp.

### Г.9 `app/ozon.py`

```
class OzonClient:
    def __init__(self, base, client_id, api_key, http): ...

    async def category_tree(self) -> list[dict]
        # POST /v1/description-category/tree
    async def category_attributes(self, category_id: int, type_id: int) -> list[dict]
        # POST /v1/description-category/attribute
    async def attribute_values(self, attribute_id: int, category_id: int,
                               type_id: int) -> list[dict]
        # POST /v1/description-category/attribute/values
        # с пагинацией last_value_id (внутри цикл while has_next)
    async def download_template(self, category_id: int, type_id: int) -> bytes
        # binary xlsx

    async def import_products(self, items: list[dict]) -> str
        # POST /v3/product/import → task_id
    async def import_status(self, task_id: str) -> dict
        # POST /v1/product/import/info
    async def import_wait(self, task_id: str,
                          interval: float = 5, max_attempts: int = 60) -> dict
```

Env: `OZON_BASE`, `OZON_CLIENT_ID`, `OZON_API_KEY`. Headers: `Client-Id`, `Api-Key`.

### Г.10 `app/wb.py`

```
class WBClient:
    def __init__(self, base, token, http): ...

    async def subjects_tree(self) -> list[dict]
        # GET /content/v2/object/parent/all?locale=ru
    async def subject_charcs(self, subject_id: int) -> list[dict]
        # GET /content/v2/object/charcs/{id}?locale=ru
    async def directory_values(self, name: str) -> list[dict]
        # GET /content/v2/directory/{name}?locale=ru (colors/kinds/countries/tnved)

    async def upload_cards(self, cards: list[dict]) -> dict
        # POST /content/v2/cards/upload — синхронный ответ
    async def upload_status(self, vendor_codes: list[str]) -> dict
        # POST /content/v2/cards/upload/list
```

Env: `WB_BASE`, `WB_TOKEN`. Header: `Authorization`.

### Г.11 `app/reports.py`

```
def build_progress_msg(stage: str, batch_id: str, **stats) -> str
def build_final_report_md(report: Report) -> str
    # Markdown:
    # *Партия {batch_id}*: 28/30 успехов
    # ✅ Ozon: ABC-001, ABC-001x2, ...
    # ❌ Ozon ошибки: ABC-001x3 — color not in dictionary
    # ✅ WB: ...
    # ⚠️ Warnings: ...
```

### Г.12 `app/pipeline.py`

См. раздел Д ниже — это основной модуль.

### Г.13 `app/main.py`

```
app = FastAPI(title="Контент завод backend")

@app.on_event("startup")
async def startup():
    # init httpx.AsyncClient (shared), KieAIClient, S3Client, TelegramClient

@app.get("/healthz")
async def health(): return {"status": "ok"}

@app.post("/api/run", status_code=202)
async def run(req: RunRequest, bg: BackgroundTasks):
    bg.add_task(pipeline.run_batch, req, deps=app.state.deps)
    return {"batch_id": req.batch_id, "queued": True, "received_at": utcnow()}
```

uvicorn на 127.0.0.1:8000 (только локально; n8n стучится через docker bridge).

---

## Д. Pipeline-логика (`app/pipeline.py`)

Строгий порядок и разбивка по корутинам:

```
async def run_batch(req: RunRequest, deps: Deps) -> None:
    tg, kie, s3, ozon, wb = deps.tg, deps.kie, deps.s3, deps.ozon, deps.wb
    await tg.send(req.chat_id, f"🟦 Запускаю партию {req.batch_id} ({len(req.products)} товаров)")

    sem = asyncio.Semaphore(settings.MAX_PARALLEL_PRODUCTS)
    states = [ProductState.from_in(p) for p in req.products]

    # Этап 1: фото — параллельно по товарам, с семафором
    await asyncio.gather(*[
        process_product_images(state, req, sem, deps) for state in states
    ])
    await tg.send(req.chat_id, f"📸 Фото готовы: {sum(1 for s in states if len(s.images)==4)}/{len(states)}")

    # Этап 2: категории — батчем (один запрос дерева на партию + LLM по каждому товару)
    ozon_tree = await ozon.category_tree()
    wb_tree   = await wb.subjects_tree()
    await asyncio.gather(*[
        match_category(state, ozon_tree, wb_tree, deps) for state in states
    ])
    await tg.send(req.chat_id, "📂 Категории определены")

    # Этап 3: справочники + шаблоны (дедуп по уникальной паре ozon_cat+wb_subj)
    unique_cats = dedup_categories(states)
    cat_data: dict[tuple, CategoryData] = {}
    for cat_key in unique_cats:
        cat_data[cat_key] = await load_category_data(cat_key, deps)
    await tg.send(req.chat_id, f"📋 Шаблоны и справочники: {len(unique_cats)} категорий")

    # Этап 4: расширение до 3 SKU + текстовая генерация (LLM)
    await asyncio.gather(*[
        build_skus_and_texts(state, cat_data, deps) for state in states
    ])

    # Этап 5: заливка на Ozon и WB — параллельно
    ozon_report, wb_report = await asyncio.gather(
        upload_ozon(states, cat_data, deps),
        upload_wb(states, cat_data, deps),
    )

    # Этап 6: финальный отчёт
    report = build_report(req.batch_id, states, ozon_report, wb_report)
    await tg.send(req.chat_id, build_final_report_md(report), parse_mode="Markdown")
```

Где:

```
async def process_product_images(state, req, sem, deps):
    async with sem:
        # 1. tg getFile + download
        path = await deps.tg.get_file_path(state.tg_file_id)
        raw  = await deps.tg.download_file(path)

        # 2. PUT исходного фото в S3
        src_key = deps.s3.build_key(req.batch_id, state.sku, "src")
        state.src_url = await deps.s3.put_public(src_key, raw, "image/jpeg")
        await deps.tg.send(req.chat_id, f"📥 {state.sku}: исходное фото в S3")

        # 3. main: image-to-image, ref=src
        main_url_kie = await deps.kie.generate_image(
            model=settings.KIE_IMAGE_MODEL,
            prompt=build_main_prompt(state.name, brand=None),
            input_urls=[state.src_url],
            aspect_ratio="3:4", resolution="2K"
        )
        # 4. скачать и залить в S3 как {sku}_main.jpg
        main_bytes = await deps.kie.fetch(main_url_kie)
        main_key = deps.s3.build_key(req.batch_id, state.sku, "main")
        state.images["main"] = await deps.s3.put_public(main_key, main_bytes)

        # 5. pack2/pack3/extra параллельно, ref=main
        pack2, pack3, extra = await asyncio.gather(
            gen_and_upload(state, "pack2", build_pack_prompt(state.name, 2), deps, req),
            gen_and_upload(state, "pack3", build_pack_prompt(state.name, 3), deps, req),
            gen_and_upload(state, "extra", build_extra_prompt(state.name), deps, req),
        )
        state.images.update(pack2=pack2, pack3=pack3, extra=extra)
        await deps.tg.send(req.chat_id, f"🖼 {state.sku}: 4/4 фото готовы")
```

`gen_and_upload` — повторяет шаги 3–4 для tag.

`match_category` — вызывает `kie.chat_json(system=..., user=...)` с обрезанным деревом и парсит `{ozon_id, ozon_type_id, wb_id, score}`.

`load_category_data(cat_key)`:
```
async def load_category_data(cat_key, deps):
    ozon_attrs = await deps.ozon.category_attributes(cat_key.ozon_id, cat_key.ozon_type_id)
    ozon_vals  = {}
    for a in ozon_attrs:
        ozon_vals[a["id"]] = await deps.ozon.attribute_values(a["id"], cat_key.ozon_id, cat_key.ozon_type_id)
    wb_charcs = await deps.wb.subject_charcs(cat_key.wb_id)
    wb_vals   = {}
    for c in wb_charcs:
        if c.get("dictionary"):
            wb_vals[c["charcID"]] = await deps.wb.directory_values(c["dictionary"])
    template_bytes = await deps.ozon.download_template(cat_key.ozon_id, cat_key.ozon_type_id)
    template = OzonTemplate.from_bytes(template_bytes)  # парсит data validations
    return CategoryData(ozon_attrs, ozon_vals, wb_charcs, wb_vals, template)
```

`build_skus_and_texts(state, cat_data, deps)`:
- `state.skus_3 = expand_to_3_skus(state)` — правила §5.2;
- LLM-вызов `kie.chat_json` для генерации `title_ozon`, `title_wb_short`, `title_wb_full`, `annotation_ozon`, `composition_wb`;
- маппинг полей через `pick_from_dict`, мультивыбор через `;`.

`upload_ozon(states, cat_data, deps)`:
- собрать `items[]` из всех 3-SKU всех товаров;
- POST /v3/product/import;
- polling /v1/product/import/info до `imported|failed`;
- собрать `OzonReport`.

`upload_wb(states, cat_data, deps)`:
- собрать cards[];
- POST /content/v2/cards/upload (синхронный ответ);
- polling /content/v2/cards/upload/list для подтверждения;
- собрать `WBReport`.

**Ошибки внутри per-product корутины** — НЕ роняют партию. Каждый `await` обёрнут в try/except, ошибка кладётся в `state.errors`, переход к следующему этапу. Финальный отчёт показывает что прошло, что нет.

**Семафор** `MAX_PARALLEL_PRODUCTS = 3` (по умолчанию) — между товарами; внутри товара main → pack2/3/extra параллельны без доп. семафора (3 параллельных kie.ai-таски — приемлемо).

**Промежуточные пинги** — на каждый завершённый этап товара (`📥 {sku}: исходное фото в S3`, `🖼 {sku}: 4/4 фото готовы`). Чтобы не спамить — минимум 1 сообщение на товар на этап. Финальный отчёт — markdown.

---

## Е. Изменения в n8n

### Е.1 Что удалить из `wf_main_v8.json`

| Нода (по `name`) | Действие |
|---|---|
| `Execute Workflow: SUB_WF_1` | удалить |
| `Telegram: статус «фото готовы»` | удалить |
| `Execute Workflow: SUB_WF_2` | удалить |
| `Telegram: статус «шаблоны готовы»` | удалить |
| `Execute Workflow: SUB_WF_3` | удалить |
| `Код: финальный отчёт MD` | удалить |
| `Telegram: финальный отчёт` | удалить |

### Е.2 Что добавить

После `Set: собрать batch` (которая формирует объект `{ batch_id, chatId, products: [...] }`) — **одна нода**:

| # | Имя | Тип | Параметры |
|---|---|---|---|
| N | `HTTP: вызов Python /api/run` | `n8n-nodes-base.httpRequest` | method=POST, url=`={{$env.CZ_BACKEND_URL}}/api/run`, sendBody=true, contentType=json, jsonBody = маппинг batch → `{batch_id, chat_id, products: [{idx, sku, name, tg_file_id}]}`. timeout=10000ms (мы не ждём пайплайна, только подтверждение очереди). onError=continueRegularOutput. |
| N+1 | `Telegram: 🚀 Запускаю...` | `n8n-nodes-base.telegram` | chatId={{$json.chat_id}}, text=`🚀 Запускаю обработку партии. Прогресс пришлю отдельными сообщениями.` |

Существующая нода `Telegram: запускаю` (line 387 в wf_main_v8.json) переименовывается / переиспользуется под этот текст.

### Е.3 Что в env n8n-контейнера

```
CZ_BACKEND_URL=http://host.docker.internal:8000
# fallback: http://172.17.0.1:8000
```

Тестируется отдельно: из контейнера `docker exec n8n curl http://host.docker.internal:8000/healthz`.

### Е.4 SUB_WF_1, SUB_WF_2, SUB_WF_3

Деактивировать (`active=false` в БД n8n или через UI). Оставляем JSON как архив, чтобы можно было откатиться. Не удаляем физически, но и не импортируем заново.

### Е.5 State machine в `Код: накопить и распарсить вход`

Остаётся как есть в `wf_main_v8.json` (idle → photos → names → confirm → running). Только в состоянии `running` его выход направляется на `HTTP: вызов Python /api/run`.

`tg_file_id` для каждого продукта парсер уже кладёт в сессию (см. строки 35–69 `wf_main_v8.json`). Маппинг в JSON для Python:
```
products = batch.products.map(p => ({
  idx: p.idx, sku: p.sku, name: p.name, tg_file_id: p.photo.file_id
}))
```

---

## Ж. Деплой

### Ж.1 Файловая структура на сервере

```
/home/albert/cz-backend/
├── .env                     # секреты (TG_BOT_TOKEN, KIE_API_KEY, S3_*, OZON_*, WB_TOKEN)
├── .venv/                   # python -m venv
├── app/                     # код из репо
├── requirements.txt
├── tests/
└── scripts/
```

### Ж.2 systemd unit `cz-backend.service`

```ini
[Unit]
Description=Контент завод Python backend (FastAPI/uvicorn)
After=network.target

[Service]
Type=simple
User=albert
WorkingDirectory=/home/albert/cz-backend
EnvironmentFile=/home/albert/cz-backend/.env
ExecStart=/home/albert/cz-backend/.venv/bin/uvicorn app.main:app \
          --host 127.0.0.1 --port 8000 --workers 1 --log-level info
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### Ж.3 Команды

```bash
# первый деплой
sudo cp /home/albert/cz-backend/scripts/cz-backend.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable cz-backend
sudo systemctl start cz-backend

# обновление
cd /home/albert/cz-backend
git pull
.venv/bin/pip install -r requirements.txt
sudo systemctl restart cz-backend

# логи
journalctl -u cz-backend -f
```

uvicorn на 127.0.0.1:8000 — только локально. nginx на хосте может проксировать `https://contentzavodprofit.ru/cz-api/*` если понадобится, но n8n-контейнер ходит напрямую через docker bridge.

### Ж.4 Доступ из n8n-контейнера

Проверить два варианта:
```bash
docker exec -it n8n_container curl http://host.docker.internal:8000/healthz
docker exec -it n8n_container curl http://172.17.0.1:8000/healthz
```

Тот, что работает, — записать в env n8n как `CZ_BACKEND_URL`.

### Ж.5 Безопасность

- uvicorn только на 127.0.0.1 (никаких 0.0.0.0).
- `.env` mode 0600, владелец albert.
- Никаких секретов в логах journald (TelegramClient логирует chat_id, не текст; KieAIClient не логирует body).
- Опционально: добавить простую проверку shared secret header `X-Internal-Token` между n8n и Python (env `INTERNAL_TOKEN`).

---

## З. .gitignore + .env.example

### З.1 `.gitignore`

```
# python
__pycache__/
*.pyc
.venv/
venv/
.pytest_cache/
.mypy_cache/
.ruff_cache/

# env / secrets
.env
.env.local
.env.*.local
*.pem
*.key

# logs
*.log
logs/

# OS / IDE
.DS_Store
Thumbs.db
.idea/
.vscode/

# проектные временные
snapshot_server/
modules/_build_*.py
modules/*.bak
modules/*_v*.json     # старые версии n8n-модулей не лежат в этом репо
db.sqlite
*.sqlite
tmp/
build/
dist/
*.egg-info/
```

### З.2 `.env.example`

```
# ── Telegram ─────────────────────────────────────────
TG_BOT_TOKEN=your-bot-token-here
TG_API_BASE=https://api.telegram.org

# ── kie.ai ───────────────────────────────────────────
KIE_BASE=https://api.kie.ai
KIE_API_KEY=your-kie-key-here
KIE_IMAGE_MODEL=gpt-image-2-image-to-image
KIE_LLM_MODEL=gpt-5-2
KIE_POLL_INTERVAL_SEC=5
KIE_POLL_MAX_ATTEMPTS=60

# ── Yandex S3 ────────────────────────────────────────
S3_ENDPOINT=https://storage.yandexcloud.net
S3_REGION=ru-central1
S3_BUCKET=cz-content-zavod-prod
S3_ACCESS_KEY=your-access-key-here
S3_SECRET_KEY=your-secret-key-here
S3_PUBLIC_BASE=https://storage.yandexcloud.net/cz-content-zavod-prod

# ── Ozon Seller ──────────────────────────────────────
OZON_BASE=https://api-seller.ozon.ru
OZON_CLIENT_ID=
OZON_API_KEY=

# ── Wildberries Content ──────────────────────────────
WB_BASE=https://content-api.wildberries.ru
WB_TOKEN=

# ── Параллелизм / runtime ────────────────────────────
MAX_PARALLEL_PRODUCTS=3
HTTP_TIMEOUT_SEC=60
LOG_LEVEL=INFO
```

---

## И. README.md / ARCHITECTURE.md / DEPLOY.md

### И.1 `README.md`

Содержание:
1. Что это: гибридный бэкенд для генерации/заливки карточек на Ozon+WB.
2. Юзер-сценарий: пользователь шлёт боту 10 наименований + 10 фото → через 5–10 минут получает отчёт о публикации.
3. Стек (1 абзац): n8n (Telegram-фронт) + Python FastAPI/asyncio + kie.ai + Yandex S3.
4. Быстрый старт для разработчика: `python -m venv .venv && pip install -r requirements.txt && cp .env.example .env && uvicorn app.main:app --reload`.
5. Тесты: `pytest`.
6. Где искать что: ссылки на `ARCHITECTURE.md` и `DEPLOY.md`.

### И.2 `ARCHITECTURE.md`

Содержание:
1. Высокоуровневая диаграмма (см. § А.1).
2. Разделение ответственности: n8n (UX) vs Python (бизнес-логика).
3. Контракт `/api/run` (см. § В).
4. Структура модулей `app/` (см. § Б).
5. Последовательность пайплайна с диаграммой (см. § Д).
6. Параллелизм: семафоры, gather, retry-стратегия.
7. Ошибки и их обработка.
8. Логи и наблюдаемость.

### И.3 `DEPLOY.md`

Содержание:
1. Требования: Ubuntu 24.04, Docker (для n8n), Python 3.12+.
2. Yandex S3: создание бакета, проверка public-read (см. integration_plan §3.5).
3. Деплой Python: clone, venv, .env, systemd unit (см. § Ж).
4. Деплой n8n WF: импорт `n8n/wf_main.json`, прописать `CZ_BACKEND_URL`.
5. Smoke-тест (см. § Л шаг 14).
6. Откат: `systemctl stop cz-backend`, реактивировать SUB_WF_1/2/3.

---

## К. Тесты

### К.1 `tests/test_kie_ai.py`

- мок `httpx.AsyncClient` через `respx`/`pytest-httpx`;
- `create_image_task` возвращает taskId;
- `poll_image_task` цикл: 2 раза `state=generating`, 1 раз `state=success` → возвращает url;
- `poll_image_task` `state=fail` → `KieAIError`;
- `poll_image_task` 60 раз `state=generating` → `KieAITimeout`;
- `chat_json` — корректный парсинг content; невалидный JSON → 1 retry → ошибка.

### К.2 `tests/test_s3.py`

- по умолчанию SKIP, активируется флагом `--integration` (через pytest fixture);
- реальный S3 (по env): `put_public("test/smoke.txt", b"ok")` → `requests.get(public_url)` 200 → `body == "ok"`;
- проверка `Content-Type` и `x-amz-acl`.

### К.3 `tests/test_rules.py`

Юнит-тесты §5.2:
- `expand_to_3_skus({sku:"A", name:"X", weight:97, dims:{l:10,w:5,h:3}})`:
  - x1: weight_unit=97, weight_packed=100, dims=(11,6,4) если from_internet, иначе (10,5,3);
  - x2: вес*2 → 200, dims = unit×qty по меньшей стороне;
  - x3: аналогично.
- `pick_from_dict(["Красный","Синий"], "красный")` → ("Красный", was_substituted=True/False в зависимости от стратегии).
- `join_multivalue(["a","b"])` → `"a;b"` (без пробелов).
- `limit_chars("...", 60)` — обрезает с многоточием.
- `nds_value()` → 22.

### К.4 `tests/test_excel.py`

- открывает `tests/fixtures/ozon_template_sample.xlsx`;
- проверяет, что `read_dictionaries()` возвращает корректные списки для колонок с Data Validation;
- `fill_rows([{...}])` → `save()` → reopen → проверка значений.

### К.5 `tests/test_pipeline.py`

- мокает все клиенты (`TelegramClient`, `KieAIClient`, `S3Client`, `OzonClient`, `WBClient`);
- запускает `run_batch()` на тестовом RunRequest (1 товар);
- проверяет последовательность вызовов и итоговый report.

### К.6 `tests/test_e2e.py`

- по умолчанию: с моками — конец-в-конец (без сети);
- по флагу `--live`: реальный прогон (нужны все env). Создаёт тестовый batch_id, ждёт sendMessage в TG mock-bot или реальный chat.

---

## Л. Шаги выполнения для главного агента

> Не делать SSH/git push в рамках этого плана. Только подготовка.

1. **Создать структуру папок** `kontent-zavod-wb/` со всеми поддиректориями (`app/`, `n8n/`, `tests/`, `scripts/`, `plan/`).
2. **Написать `.gitignore`** (см. § З.1) и **`.env.example`** (см. § З.2).
3. **Написать `requirements.txt`**:
   ```
   fastapi>=0.115
   uvicorn[standard]>=0.30
   httpx>=0.27
   pydantic>=2.7
   pydantic-settings>=2.4
   tenacity>=9.0
   boto3>=1.35
   aiobotocore>=2.13   # async S3
   openpyxl>=3.1
   pillow>=10.4        # post-processing 3:4 fallback
   aiofiles>=24.1
   python-multipart>=0.0.9
   pytest>=8.3
   pytest-asyncio>=0.24
   pytest-httpx>=0.30
   respx>=0.21
   ```
4. **Написать модули `app/` в порядке зависимостей:**
   1. `config.py`
   2. `models.py`
   3. `telegram.py`
   4. `s3.py`
   5. `kie_ai.py`
   6. `prompts.py`
   7. `rules.py`
   8. `excel.py`
   9. `ozon.py`
   10. `wb.py`
   11. `reports.py`
   12. `pipeline.py`
   13. `main.py`
5. **Написать тесты** в порядке: `test_rules.py` → `test_kie_ai.py` → `test_excel.py` → `test_telegram.py` → `test_s3.py` → `test_pipeline.py` → `test_e2e.py`. Прогнать `pytest -m "not integration and not live"` локально.
6. **Написать docs:** `README.md`, `ARCHITECTURE.md`, `DEPLOY.md`, `n8n/README.md`.
7. **Подготовить `n8n/wf_main.json`:**
   - взять `modules/wf_main_v8.json` за основу;
   - удалить ноды из § Е.1;
   - добавить `HTTP: вызов Python /api/run` и (при необходимости) переиспользовать `Telegram: запускаю` под текст «🚀 Запускаю...»;
   - проверить, что `Set: собрать batch` маппит `tg_file_id` правильно;
   - сохранить как `n8n/wf_main.json`.
8. **`git init`** в `kontent-zavod-wb/`, начальный commit «Initial: hybrid Python+n8n backend skeleton».
9. **`git remote add origin https://github.com/sergeyoooo4321-pixel/kontent-zavod-wb`** (репо ещё надо создать в GitHub-UI — попросить пользователя или использовать `gh repo create` с PAT).
10. **`git push -u origin main`** — потребует PAT (Personal Access Token), запросить у пользователя.
11. **SCP на сервер:**
    ```
    scp -r kontent-zavod-wb/ albert@2.26.65.241:/home/albert/cz-backend/
    ssh albert@2.26.65.241 "cd /home/albert/cz-backend && python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    ```
12. **Установить systemd unit:**
    ```
    sudo cp /home/albert/cz-backend/scripts/cz-backend.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now cz-backend
    curl http://127.0.0.1:8000/healthz
    ```
13. **Импорт обновлённого `wf_main.json` в n8n** через UI или `docker exec n8n n8n import:workflow --input=/tmp/wf_main.json --separate`. Деактивировать старые SUB_WF_1/2/3. Прописать env `CZ_BACKEND_URL`. Перезапустить n8n: `docker compose restart n8n`.
14. **Smoke-тест без Ozon/WB ключей:**
    - в Telegram-боте прислать 1 строку «Тестовый товар; TST-1» + 1 фото;
    - дойти до confirm → нажать «запускаю»;
    - n8n должен ответить «🚀 Запускаю...»;
    - в journald `journalctl -u cz-backend -f` — увидеть прогресс;
    - в S3-бакете появятся `{batch_id}/TST-1_src.jpg`, `_main.jpg`, `_pack2.jpg`, `_pack3.jpg`, `_extra.jpg`;
    - в Telegram появятся пинги «📸 фото готовы»;
    - на этапе категорий/заливки — упадёт с понятной ошибкой «OZON_API_KEY not set» (это ОК, ключи введём позже).
15. **Отчитаться** пользователю: что развернуто, что протестировано, что осталось (ввести Ozon/WB ключи в `.env`, перезапустить `cz-backend`).

---

## М. Критические места (для главного агента — обратить внимание)

1. **Доступ из n8n-контейнера к Python на хосте.** Сначала проверить `host.docker.internal`, потом `172.17.0.1`. На Linux `host.docker.internal` работает только если контейнер запущен с `--add-host=host.docker.internal:host-gateway` или в Docker Compose с этим флагом. Если нет — перезапустить n8n с флагом ИЛИ использовать IP моста.
2. **Telegram `file_path` живёт ~1 час.** Скачивание исходного фото и заливка в S3 должны быть **первым шагом** пайплайна, не откладывая.
3. **Yandex S3 per-object ACL.** Бакет может быть public, но `put_object` ОБЯЗАТЕЛЬНО с `ACL='public-read'` — иначе объект приватный.
4. **`response_format: json_object` для gpt-5-2.** Может не поддерживаться. План Б — убрать поле, парсить через try/except + retry с подсказкой «верни строго JSON».
5. **Ozon `attribute/values` пагинация.** `last_value_id` обязательно в цикле, иначе получим первую страницу 5000 значений и пропустим остальные.
6. **WB cards/upload** возвращает синхронный ответ с per-item ошибками, но статус публикации — отдельный poll через `/upload/list`. Не путать.
7. **Семафор `MAX_PARALLEL_PRODUCTS`.** Дефолт 3 — безопасный. На большом тарифе kie.ai можно 5–10.
8. **Внутри товара pack2/pack3/extra параллельно** — это 3 одновременных kie.ai-таски. Если rate-limit — снизить до последовательного.
9. **Маппинг `tg_file_id` в n8n.** В `Set: собрать batch` убедиться, что `products[i].photo.file_id` корректно проброшен как `tg_file_id` для Python.
10. **`.env` НЕ коммитить.** Двойная проверка `.gitignore` перед `git push`.
11. **Логирование секретов.** Никаких токенов в `print()`/`logger.info`. KieAIClient логирует только taskId, не body.
12. **WB API URL** в текущем плане — `content-api.wildberries.ru`, в новых документах WB иногда `suppliers-api.wildberries.ru`. Проверить актуальный при подключении ключа.

---

## Финал

План полностью покрывает ТЗ v1.4 в гибридной архитектуре. n8n остаётся минимальным фронтом (state machine + 1 HTTP вызов), всё остальное — testable Python-бэкенд. Контракт между ними зафиксирован в § В. Главный агент может стартовать с § Л шаг 1 и идти линейно.
