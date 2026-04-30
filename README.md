# Контент завод

Гибридный бэкенд для генерации и автозаливки карточек товаров на **Ozon** и **Wildberries**. Принимает 10 фото товаров через Telegram-бота и за ~5–10 минут создаёт по каждому товару 4 фото (3:4) + 3 SKU (одиночка / x2 / x3) и публикует карточки на оба маркетплейса.

## Стек

| Слой | Технология | Роль |
|---|---|---|
| Frontend (UX) | **n8n 2.18** | Telegram Trigger, state-machine ввода, базовые ответы пользователю |
| Backend (бизнес-логика) | **Python 3.12 + FastAPI + asyncio** | kie.ai генерация, S3, Ozon/WB API, Excel шаблоны, отчёты |
| AI | **kie.ai** | gpt-image-2-image-to-image (фото) + gpt-5-2 (LLM) |
| Хранилище | **Yandex Object Storage** | публичные URL для маркетплейсов |
| Деплой | **systemd + Docker** | uvicorn на 127.0.0.1:8000, nginx + n8n в Docker |

## Юзер-сценарий

1. Пользователь шлёт `/start` в `@Content_Zavod_Karusel_bot`.
2. Бот: «жди фото — кидай по одному».
3. Пользователь шлёт фото товаров (1..10) — после каждого бот: «фото N принято».
4. Пользователь жмёт «✅ Перейти к названиям».
5. Для каждого фото пользователь шлёт `Название, артикул`.
6. Бот показывает свод и две кнопки: **🚀 Генерация** / **❌ Отмена**.
7. **🚀 Генерация** → n8n шлёт POST на Python-бэкенд → возвращает «🚀 Запускаю...».
8. Python проходит этапы: фото → категории → шаблоны → заполнение → заливка. Шлёт прогресс в Telegram.
9. Финальный Markdown-отчёт: какие SKU опубликованы, какие отклонены и почему.

## Документация

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — схема, разделение n8n/Python, контракты
- [`DEPLOY.md`](DEPLOY.md) — как развернуть с нуля
- [`plan/HYBRID_PLAN.md`](plan/HYBRID_PLAN.md) — детальный план реализации
- [`n8n/README.md`](n8n/README.md) — импорт workflow

## Быстрый старт (разработка)

```bash
git clone https://github.com/sergeyoooo4321-pixel/kontent-zavod-wb
cd kontent-zavod-wb

python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# отредактируй .env, подставь реальные токены

uvicorn app.main:app --reload --port 8000
```

## Тесты

```bash
pytest                      # юнит-тесты с моками
pytest --integration        # + реальный S3 (нужны env-переменные)
pytest --live               # full e2e через реальные API (медленно, требует все ключи)
```

## ТЗ

Полный текст в [`../ТЗ_Агент_1_рабочий_сценарий.md`](../ТЗ_Агент_1_рабочий_сценарий.md). Ключевые требования §3–§6 реализованы в `app/rules.py` и `app/pipeline.py`.

## Структура

```
kontent-zavod-wb/
├── app/                  # Python модули (FastAPI, клиенты, pipeline)
├── n8n/                  # workflow JSON для импорта в n8n
├── tests/                # pytest
├── scripts/              # systemd unit, deploy скрипты
├── plan/                 # архитектурные документы
├── README.md
├── ARCHITECTURE.md
├── DEPLOY.md
├── .env.example
└── requirements.txt
```
