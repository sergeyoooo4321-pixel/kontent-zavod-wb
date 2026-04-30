# Архитектура «Контент завод»

## Схема

```
┌──────────────┐    ┌──────────────────────────────────────────────┐
│  Telegram    │    │  VPS 2.26.65.241                             │
│  пользователь│───►│  ┌──────────────────────────────────────────┐│
│              │    │  │  nginx (TLS) на :443                     ││
│              │    │  │   /tg/  ──┐                              ││
│              │    │  └───────────┼──────────────────────────────┘│
│              │    │              ▼                               │
│              │    │  ┌──────────────────────────────────────────┐│
│              │    │  │  Python FastAPI   (uvicorn :8000)        ││
│              │    │  │   • /tg/webhook → tg_handler             ││
│              │    │  │   • /api/run    → pipeline.run_batch     ││
│              │    │  │   • /healthz                             ││
│              │    │  │                                          ││
│              │    │  │   tg_handler:                            ││
│              │    │  │     state machine: idle→photos→names→    ││
│              │    │  │       confirm→running                    ││
│              │    │  │     in-memory sessions[chat_id]          ││
│              │    │  │                                          ││
│              │    │  │   pipeline.py (asyncio):                 ││
│              │    │  │     1. tg getFile + S3 src               ││
│              │    │  │     2. kie.ai 4 фото на товар (gather)   ││
│              │    │  │     3. categories Ozon+WB (LLM)          ││
│              │    │  │     4. шаблоны + справочники             ││
│              │    │  │     5. fill rules §5.2                   ││
│              │    │  │     6. upload Ozon + WB                  ││
│              │    │  │     7. report → telegram                 ││
│              │    │  └──────────────────────────────────────────┘│
│              │    └─────────────┬─────────────┬─────────────┬────┘
│              │                  ▼             ▼             ▼
│              │           ┌──────────┐  ┌──────────┐  ┌──────────────┐
│              │           │ kie.ai   │  │Yandex S3 │  │ Ozon Seller  │
│              ◀───────────┤(image-to-│  │ public-  │  │  + WB Content│
│ ack/progress │           │  image + │  │  read    │  │   API        │
│ /report      │           │  gpt-5-2)│  └──────────┘  └──────────────┘
└──────────────┘           └──────────┘
```

## Почему чистый Python (без n8n)

Мы пробовали гибрид с n8n как «тонким Telegram-фронтом», но столкнулись с серией проблем в его песочнице/webhook-системе:

- Code-нода блокирует `require('crypto')`, `globalThis.crypto`, `fetch` — нужны workaround'ы
- Webhook secret token валидация неконсистентна между TG trigger и webhook node
- AWS-auth httpRequest падает на ReadStream (n8n стримит binary, aws4 хочет Buffer)
- Template-парсер ломается на вложенных `{...}` (например, `reply_markup`)
- Сложный publish-vs-draft workflow в n8n 2.x
- Каждый фикс = export → правка JSON → SFTP → docker cp → import → publish → restart (~2 минуты на любое изменение)
- Цикл обратной связи: 2 минуты vs `Ctrl+S` в Python (~2 секунды)

Прямой Python-handler оказался в **разы быстрее в разработке** и **надёжнее в работе**.

## Контракт `Telegram → Python`

### Endpoint: `POST /tg/webhook`

Вход — стандартный Telegram update:
```json
{
  "update_id": 12345,
  "message": {
    "message_id": 678,
    "from": {"id": 123, ...},
    "chat": {"id": 123, "type": "private"},
    "date": 1234567890,
    "text": "/start"     // или photo: [...] или другое
  }
}
```

Выход — `200 {"ok": true}` мгновенно. Реальная обработка идёт в `BackgroundTasks`.

### Внутренний endpoint `/api/run`

Сохранён для **ручного тестирования** пайплайна без Telegram:
```json
POST /api/run
{
  "batch_id": "test-001",
  "chat_id": 123,
  "products": [
    {"idx": 0, "sku": "...", "name": "...", "tg_file_id": "AgACAg..."}
  ]
}
→ 202 {"batch_id": "test-001", "queued": true, ...}
```

## State machine (app/tg_handler.py)

Состояния сессии per `chat_id`:

| Phase | Что принимаем | Кнопки |
|---|---|---|
| `idle` | `/start`, `🚀 Новая партия` | главное меню (info-кнопки этапов) |
| `photos` | фото товаров (1..10) | `✅ Перейти к названиям`, `🔄 Сбросить` |
| `names` | `Название, артикул` (по строке на товар) | `🔄 Сбросить` |
| `confirm` | `🚀 Генерация` или `❌ Отмена` | две кнопки |
| `running` | (всё игнорируется кроме `🔄 Сбросить`) | минимум |

Универсальные команды (работают всегда): `/reset`, `/status`, `/help`.

Состояние хранится **in-memory** в `_sessions: dict[int, TgSession]`. При рестарте сервиса — сессии теряются. Для production-нагрузок можно перенести в Redis.

## Pipeline (app/pipeline.py)

Главная корутина `run_batch(req, deps)` запускается из `tg_handler` при переходе в фазу `running`:

```python
1. tg.send "🟦 Запускаю партию"
2. asyncio.gather(per-product, sem=Semaphore(MAX_PARALLEL_PRODUCTS=3)):
     getFile → S3 src.jpg
     vision-LLM «арт-директор» → JSON-бриф дизайна
     gen main (ref=src) → S3
     asyncio.gather(pack2, pack3, extra с ref=main) → S3
     tg.send media_group + ZIP + ссылки
3. ozon.category_tree() + wb.subjects_tree()
4. asyncio.gather: match_category(state) per product           (LLM)
5. for each unique (ozon_id, type_id, wb_id): load_category_data
     (attrs+attribute_values для Ozon, charcs+directory для WB)
6. asyncio.gather: build_skus_and_texts per state:
     for each of 3 SKU (qty=1,2,3):
       LLM #1 → titles (ozon/wb_short/wb_full/annotation/composition)
       LLM #2 → ozon attribute values  → mapping.map_ozon_attributes
       LLM #3 → wb characteristic vals → mapping.map_wb_characteristics
7. asyncio.gather: upload_ozon, upload_wb (return_exceptions=True)
   — SKU с None в state.attributes_ozon/state.characteristics_wb
     (required не нашёлся) → сразу в Report.errors, не уходят в API
8. tg.send build_final_report_md(...)
```

### Параллелизм
- `MAX_PARALLEL_PRODUCTS=3` — semaphore между товарами
- Внутри товара main/pack2/pack3/extra — 4 параллельных kie.ai запроса
- `KieAIClient._sem = Semaphore(8)` — глобальный лимит на kie.ai

### Retry
- `tenacity` декораторы: для GET — на любой `httpx.HTTPError`, для POST — только на `ConnectError`/`ConnectTimeout` (idempotency)
- 429 Too Many Requests — отдельная обработка с `Retry-After` header
- Per-SKU ошибки → `state.errors`, не валит партию

### Маппинг атрибутов и характеристик (`app/mapping.py`)

Двухступенчатый процесс «LLM raw → API payload»:

1. **LLM получает компактный список атрибутов категории** + топ-30 значений
   справочника как `examples` (полный словарь Ozon — до 5000 строк, в LLM
   не влезет и не нужен). Возвращает `{<id>: "raw value" | ["v1", "v2"]}`.
2. **Локально по полному словарю** ищем ближайшее значение через Левенштейна
   (`rules.pick_from_dict`) и собираем payload в формате конкретного API:
    * Ozon: `{complex_id, id, values: [{dictionary_value_id, value}]}`
    * WB:   `{id, value: [<v1>, <v2>, ...]}`

Подменённые значения («малиновый» → «Красный») попадают в `state.warnings`
и финальный отчёт.

### Required-policy

Если хоть один **required** атрибут/характеристика не получили значения
(LLM не вернул, или Левенштейн не нашёл совпадения в словаре), маппер
возвращает `(None, warnings)`. SKU исключается из импорта на этап 7 и
сразу попадает в `Report.errors` с причиной — квота API не тратится.

## Структура Python-модулей

| Модуль | Роль |
|---|---|
| `config.py` | pydantic-settings, env с валидацией |
| `models.py` | `RunRequest`, `ProductIn`, `ProductState`, `Report` |
| `telegram.py` | `TelegramClient` (sendMessage, getFile, downloadFile) с маскированием токена в exception messages |
| `tg_handler.py` | state machine + dispatch на pipeline |
| `kie_ai.py` | `KieAIClient`: createTask + polling + chat_json (с rate-limit handling) |
| `s3.py` | `S3Client` через aiobotocore (долгоживущий) с per-object public-read |
| `prompts.py` | сборщики промптов для image, LLM-категорий, LLM-атрибутов/характеристик |
| `rules.py` | бизнес-правила §5.2 ТЗ + Левенштейн-подбор значений |
| `mapping.py` | LLM-сырые значения → payload Ozon/WB (со словарным мэппингом) |
| `excel.py` | `OzonTemplate`: openpyxl + Data Validation (включая reference) |
| `ozon.py` | Ozon Seller API client |
| `wb.py` | WB Content API client |
| `reports.py` | Markdown отчёт |
| `pipeline.py` | оркестратор |
| `main.py` | FastAPI app + lifespan |

## Безопасность

- uvicorn слушает `0.0.0.0:8000` — но **ufw** разрешает только `127.0.0.0/8` и `172.17.0.0/16`. Внешним недоступен.
- nginx терминирует TLS на 443; внутри HTTP до Python.
- Все секреты — в `/home/albert/cz-backend/.env` mode 0600 owned by albert.
- Telegram bot token маскируется в exception messages (см. `telegram._mask_token`).
- httpx/aiobotocore логгеры на WARNING — токены не утекают в journal.
- В `RunRequest` — Pydantic валидация: `tg_file_id` `min_length=10`, `products` 1-10.

## Безопасное обновление

```bash
cd /home/albert/cz-backend
git pull
.venv/bin/pip install -r requirements.txt
sudo systemctl restart cz-backend
journalctl -u cz-backend -f
```
