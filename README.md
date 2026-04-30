# Контент завод

Чистый Python-бэкенд для генерации и автозаливки карточек товаров на **Ozon** и **Wildberries**. Принимает 10 фото товаров через Telegram-бота и за ~5–10 минут создаёт по каждому товару 4 фото (3:4) + 3 SKU (одиночка / x2 / x3) и публикует карточки на оба маркетплейса.

## Стек

| Слой | Технология |
|---|---|
| Telegram-фронт + state machine | **Python + FastAPI + asyncio** (`app/tg_handler.py`) |
| Бизнес-логика (kie.ai, S3, Ozon, WB) | **Python + httpx + aiobotocore + openpyxl** |
| AI | **kie.ai** (gpt-image-2-image-to-image + gpt-5-2 LLM) |
| Хранилище | **Yandex Object Storage** (public-read URL) |
| Инфраструктура | **systemd + nginx + Let's Encrypt** на одном VPS |

**n8n больше НЕ используется.** Раньше планировался как Telegram-фронт, но прямой Python-handler оказался надёжнее и проще.

## Юзер-сценарий

1. `/start` в `@Content_Zavod_Karusel_bot`
2. Бот: «Кидай фото товаров по одному»
3. Юзер шлёт фото 1..10. После каждого: «📷 Фото N принято»
4. Юзер жмёт **✅ Перейти к названиям**
5. Для каждого фото юзер пишет `Название, артикул`
6. Бот показывает свод и кнопки **🚀 Генерация** / **❌ Отмена**
7. **🚀 Генерация** → пайплайн идёт ~5–10 минут, бот шлёт прогресс этапов
8. Финальный Markdown-отчёт: какие SKU опубликованы, какие отклонены и почему

## Архитектура одной картинкой

```
[Telegram] ──webhook──▶ nginx ──▶ Python FastAPI :8000
                                        │
                                        ├─ /tg/webhook ── handler (state machine)
                                        │                     │
                                        │                     ▼
                                        │              pipeline.run_batch (async)
                                        │                     │
                                        │       ┌─────────────┼──────────┬───────────┐
                                        │       ▼             ▼          ▼           ▼
                                        │   kie.ai      Yandex S3     Ozon API   WB API
                                        │
                                        └─ /healthz, /api/run (manual test)
```

Подробности в [`ARCHITECTURE.md`](ARCHITECTURE.md). Деплой в [`DEPLOY.md`](DEPLOY.md).

## Быстрый старт (разработка)

```bash
git clone https://github.com/sergeyoooo4321-pixel/kontent-zavod-wb
cd kontent-zavod-wb

python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# отредактируй .env, подставь реальные TG_BOT_TOKEN / KIE_API_KEY / S3_*

uvicorn app.main:app --reload --port 8000
```

Чтобы Telegram доставлял webhooks локально — используй [ngrok](https://ngrok.com) или прокинь свой VPS:
```bash
curl -X POST "https://api.telegram.org/bot${TG_BOT_TOKEN}/setWebhook" \
  -d "url=https://YOUR_HOST/tg/webhook&drop_pending_updates=true"
```

## Тесты

```bash
pytest                  # 29 unit-тестов с моками
pytest --integration    # + реальный S3 (нужны env)
pytest --live           # full e2e (медленно, нужны все ключи)
```

## Структура

```
kontent-zavod-wb/
├── app/                  # 14 модулей: config, models, telegram, tg_handler,
│                         #             kie_ai, s3, ozon, wb, excel, prompts,
│                         #             rules, reports, pipeline, main
├── tests/                # pytest (29 тестов)
├── scripts/              # systemd unit + deploy.sh
├── plan/                 # архитектурные документы
├── README.md
├── ARCHITECTURE.md
├── DEPLOY.md
├── .env.example
├── requirements.txt
└── pytest.ini
```

## ТЗ

Полный текст в [`../ТЗ_Агент_1_рабочий_сценарий.md`](../ТЗ_Агент_1_рабочий_сценарий.md). Ключевые требования §3-§6 реализованы в `app/rules.py`, `app/prompts.py` и `app/pipeline.py`.
