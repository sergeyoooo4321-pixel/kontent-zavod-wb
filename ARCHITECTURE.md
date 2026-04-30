# Архитектура «Контент завод»

## Схема

```
┌──────────────┐    ┌──────────────────────────────┐    ┌───────────────────────────┐
│  Telegram    │    │  n8n  (Docker, 2.26.65.241)  │    │  Python FastAPI           │
│  пользователь│───►│  WF "Контент завод"          │───►│  cz-backend.service       │
│              │    │  • TG Trigger                │    │  127.0.0.1:8000           │
│              │    │  • State-machine парсер      │POST│  • POST /api/run (async)  │
│              │    │  • Telegram реплаи           │/api│  • GET /healthz           │
│              │    │  • HTTP → Python /api/run    │/run│                           │
└──────────────┘    └──────────────────────────────┘    │  pipeline.py (asyncio)    │
       ▲                          ▲                     │  • per-product gather()   │
       │                          │                     │  • src→S3→main→pack/extra │
       │  прогресс/отчёт          │                     │  • LLM категории/тексты   │
       │  (sendMessage напрямую    │                     │  • XLSX парсинг + filling │
       │   из Python)              │                     │  • Ozon /v3 + WB upload   │
       └──────────────────────────┴─────────────────────┤  • Markdown отчёт         │
                                                        └─┬─────────────────────────┘
                            ┌──────────────────────────────┼────────────────┐
                            ▼                              ▼                ▼
                    ┌─────────────────┐          ┌──────────────┐  ┌──────────────┐
                    │ kie.ai          │          │ Yandex S3    │  │ Ozon Seller  │
                    │ • image-2-image │          │ public-read  │  │ + WB Content │
                    │ • gpt-5-2 LLM   │          │ ru-central1  │  │   API        │
                    └─────────────────┘          └──────────────┘  └──────────────┘
```

## Разделение ответственности

### n8n — UX (Telegram-фронт)

- **Telegram Trigger** ловит входящие сообщения.
- **Code-нода парсера** реализует state-machine: `idle → photos → names → confirm → running`. Состояние хранится в `$getWorkflowStaticData('global').sessions[chatId]`.
- **Telegram-реплаи** для каждой фазы — динамическая клавиатура.
- **HTTP Request** в фазе `running` — один POST на Python-бэкенд, моментальный ответ.
- **Никакой бизнес-логики**: не парсим Excel, не генерим фото, не льём на маркетплейсы.

### Python — бизнес-логика

- Скачивание исходного фото из Telegram → S3.
- 4 параллельных kie.ai-генерации на товар (main → pack2/3/extra).
- Подбор категории Ozon/WB через LLM.
- Скачивание Excel-шаблона Ozon, парсинг Data Validation для справочников.
- Расширение до 3 SKU по правилам §5.2 ТЗ.
- LLM-генерация заголовков и описаний с лимитами 60/100 символов.
- Маппинг полей с Левенштейном для справочников + мультивыбор `;`.
- Заливка через Ozon `/v3/product/import` и WB `/content/v2/cards/upload` с polling.
- Финальный Markdown-отчёт.
- Промежуточные пинги юзеру через bot token напрямую (никаких callback'ов в n8n).

## Контракт `n8n → Python`

### Запрос: `POST http://host.docker.internal:8000/api/run`

```json
{
  "batch_id": "uuid-string",
  "chat_id": 123456789,
  "products": [
    {
      "idx": 0,
      "sku": "ABC-001",
      "name": "Кофе зерновой Арабика",
      "tg_file_id": "AgACAgIAA...",
      "brand": null
    }
  ]
}
```

Опциональный header `X-Internal-Token` для shared-secret проверки.

### Ответ: 202 Accepted, мгновенно

```json
{
  "batch_id": "uuid-string",
  "queued": true,
  "received_at": "2026-04-30T10:00:00Z"
}
```

Внутри `main.py` хендлер кладёт корутину `pipeline.run_batch()` в `BackgroundTasks` — возврат не ждёт.

## Структура Python-модулей

| Модуль | Роль |
|---|---|
| `config.py` | pydantic-settings: env-переменные с дефолтами и валидацией |
| `models.py` | Pydantic-схемы: `RunRequest`, `ProductState`, `Report` |
| `telegram.py` | `TelegramClient`: sendMessage, getFile, downloadFile (с retry) |
| `kie_ai.py` | `KieAIClient`: createTask + polling, chat_json (LLM) |
| `s3.py` | `S3Client` через aiobotocore: put_public, fetch |
| `prompts.py` | Сборщики промптов для image и LLM |
| `rules.py` | Бизнес-правила §5.2: SKU/dims/weights/limits/multi-value |
| `excel.py` | `OzonTemplate`: openpyxl + Data Validation parsing |
| `ozon.py` | `OzonClient`: tree, attributes, attribute/values (пагинация), import + polling |
| `wb.py` | `WBClient`: subjects/charcs/directory, upload + status polling |
| `reports.py` | Markdown-отчёт |
| `pipeline.py` | Оркестратор: `run_batch()` с asyncio.gather + Semaphore |
| `main.py` | FastAPI app + lifespan |

## Последовательность пайплайна

```
run_batch():
  1. send "🟦 Запускаю партию"
  2. parallel(per product, sem):
       getFile → S3 src.jpg
       generate_image(main, ref=src) → S3 main.jpg
       parallel:
         generate_image(pack2, ref=main) → S3 pack2.jpg
         generate_image(pack3, ref=main) → S3 pack3.jpg
         generate_image(extra, ref=main) → S3 extra.jpg
       send "🖼 {sku}: 4/4 фото"
  3. send "📸 фото готовы N/M"
  4. parallel(per product): match_category(LLM)
  5. send "📂 категории"
  6. for each unique category: load_category_data (attrs+values+template)
  7. parallel(per product): build_skus_and_texts (3 SKU + LLM titles)
  8. parallel: upload_ozon, upload_wb
  9. send build_final_report_md(...)
```

### Параллелизм

- `MAX_PARALLEL_PRODUCTS=3` — semaphore между товарами (rate-limit kie.ai).
- Внутри товара: main последовательно, pack2/pack3/extra параллельно (3 одновременных kie.ai-таски).
- Ozon и WB заливка параллельны.

### Ретраи

- `tenacity` декораторы: `retry_if_exception_type(httpx.HTTPError)`, 3 попытки, экспоненциальный backoff.
- На уровне пайплайна: ошибка одного SKU → `state.errors.append()`, не валит партию.

### Логирование

- `logging` стандартный, в `journald` через systemd.
- Никаких токенов в логах.
- `INFO` — переходы между этапами и важные ID.
- `WARNING` — деградации (категория не определилась, dictionary не нашёлся).
- `EXCEPTION` — необработанные.

## Безопасность

- Python слушает только `127.0.0.1:8000`.
- Опциональный `X-Internal-Token` shared secret между n8n и Python.
- `.env` mode 0600, владелец albert.
- Yandex S3 ACL: per-object public-read (бакет приватный, объекты публичные).
- Telegram chat_id whitelist можно добавить в парсере n8n при необходимости.
