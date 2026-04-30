# n8n workflow — «Контент завод (Python backend)»

Урезанный WF (13 нод) — только Telegram-фронт. Всё heavy lifting в Python.

## Импорт

```bash
docker cp wf_main.json n8n:/tmp/wf_main.json
docker exec n8n n8n import:workflow --input=/tmp/wf_main.json
docker exec n8n n8n update:workflow --id=EjcUgpRvj4ZMXXlc --active=true
docker restart n8n
```

## Env-переменные n8n-контейнера

Нужны (передавать через `-e` при `docker run`):

| Переменная | Значение |
|---|---|
| `CZ_BACKEND_URL` | `http://host.docker.internal:8000` (или `http://172.17.0.1:8000`) |
| `TG_BOT_TOKEN` | токен Telegram-бота (для нод «Telegram: запускаю» и Telegram-реплаев) |
| `INTERNAL_TOKEN` | (опц.) shared secret с Python-бэкендом |
| `NODE_FUNCTION_ALLOW_BUILTIN` | `crypto` (legacy для старых SUB_WF) |

## Ноды

| Название | Тип | Роль |
|---|---|---|
| TG Webhook | telegramTrigger | вход |
| Код: накопить и распарсить вход | code | state-machine: idle → photos → names → confirm → running |
| IF: не служебное сообщение? | if | фильтр не-сообщений |
| IF: партия собрана? | if | если фаза `confirm` + «🚀 Генерация» → батч готов |
| Telegram: запрос недостающих данных | httpRequest | Telegram sendMessage с динамической клавиатурой (по фазе) |
| Set: собрать batch | code | формирует `{batch: {batch_id, chatId, products[]}}` |
| Код: payload для Python | code | мап в формат `RunRequest` Python-бэкенда |
| HTTP: вызов Python /api/run | httpRequest | POST на `${CZ_BACKEND_URL}/api/run` (timeout 15с) |
| Telegram: 🚀 Запускаю | httpRequest | моментальный ответ юзеру «🚀 Запускаю...» |
| Error Trigger / Код: текст ошибки / IF / Telegram критическая | error-handling | ловит падения n8n |

## Деактивация старых SUB_WF

```bash
for id in cz1IntakeImagesA cz2CategoryTplsB cz3FillAndUpldC0; do
    docker exec n8n n8n update:workflow --id=$id --active=false
done
```

## Тест из контейнера

```bash
docker exec n8n curl -s http://host.docker.internal:8000/healthz
```

Должно вернуть `{"status":"ok",...}`.
