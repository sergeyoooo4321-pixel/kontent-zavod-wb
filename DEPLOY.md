# Деплой «Контент завод»

Целевой сервер: **Ubuntu 24.04** + Docker + nginx + n8n (контейнер).

## 1. Требования

| Компонент | Версия |
|---|---|
| OS | Ubuntu 24.04+ |
| Python | 3.12+ |
| Docker | 28+ (для n8n) |
| n8n | 2.18+ с env `NODE_FUNCTION_ALLOW_BUILTIN=crypto` (для legacy SUB_WF) |
| nginx | для TLS перед n8n |

## 2. Yandex Object Storage

Создать сервисный аккаунт с ролью `storage.editor` на нужный folder. Сгенерировать **HMAC-ключи** (Access Key ID + Secret).

Создать бакет (один раз):

```bash
aws --endpoint-url=https://storage.yandexcloud.net \
    s3api create-bucket --bucket cz-content-zavod-prod
```

Бакет приватный — публичный доступ даём per-object через ACL `public-read` (это делает Python-код).

## 3. Деплой Python-бэкенда

```bash
# 1. подготовка директории
sudo mkdir -p /home/albert/cz-backend
sudo chown albert:albert /home/albert/cz-backend

# 2. получить код
cd /home/albert/cz-backend
git clone https://github.com/sergeyoooo4321-pixel/kontent-zavod-wb .

# 3. виртуальное окружение
python3.12 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

# 4. .env (никогда не коммитим!)
cp .env.example .env
chmod 0600 .env
# отредактируй .env: TG_BOT_TOKEN, KIE_API_KEY, S3_*, OZON_*, WB_TOKEN

# 5. systemd unit
sudo cp scripts/cz-backend.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cz-backend

# 6. проверка
curl http://127.0.0.1:8000/healthz
journalctl -u cz-backend -n 50 -f
```

## 4. Доступ из n8n-контейнера к Python

Чтобы из контейнера n8n стучаться на хост-Python (127.0.0.1:8000 на хосте), есть два варианта:

### Вариант A: `host.docker.internal` (рекомендуется)

Контейнер n8n должен быть запущен с флагом `--add-host=host.docker.internal:host-gateway`. Если n8n запущен старым `docker run` без этого флага — пересоздать:

```bash
docker stop n8n && docker rm n8n
docker run -d --name n8n --restart unless-stopped \
    --add-host=host.docker.internal:host-gateway \
    -p 127.0.0.1:5678:5678 \
    -v /home/albert/n8n-docker/n8n_data:/home/node/.n8n \
    -e N8N_HOST=contentzavodprofit.ru \
    -e N8N_PORT=5678 \
    -e N8N_PROTOCOL=https \
    -e WEBHOOK_URL=https://contentzavodprofit.ru/ \
    -e N8N_PROXY_HOPS=1 \
    -e GENERIC_TIMEZONE=Europe/Moscow \
    -e NODE_FUNCTION_ALLOW_BUILTIN=crypto \
    -e CZ_BACKEND_URL=http://host.docker.internal:8000 \
    n8nio/n8n:latest
```

Проверка из контейнера:
```bash
docker exec n8n curl -s http://host.docker.internal:8000/healthz
```

### Вариант B: IP моста `docker0`

Узнать IP моста: `ip addr show docker0 | grep inet` — обычно `172.17.0.1`.

```bash
docker exec n8n curl -s http://172.17.0.1:8000/healthz
```

Использовать как `CZ_BACKEND_URL=http://172.17.0.1:8000`.

## 5. Импорт обновлённого n8n WF

```bash
# скопировать workflow JSON в контейнер
docker cp n8n/wf_main.json n8n:/tmp/wf_main.json
docker exec n8n n8n import:workflow --input=/tmp/wf_main.json

# активировать
docker exec n8n n8n update:workflow --id=EjcUgpRvj4ZMXXlc --active=true

# деактивировать старые SUB_WF (если ещё активны)
for id in cz1IntakeImagesA cz2CategoryTplsB cz3FillAndUpldC0; do
    docker exec n8n n8n update:workflow --id=$id --active=false
done

# рестарт чтобы n8n перечитал состояние
docker restart n8n
```

## 6. Smoke-тест

Без Ozon/WB ключей пайплайн дойдёт до этапа категорий и остановится с предупреждением (это ОК на этом этапе).

1. Открой `@Content_Zavod_Karusel_bot` в Telegram.
2. `/start`.
3. Кидай 1 фото → бот «📷 Фото 1 принято».
4. «✅ Перейти к названиям» → пиши `Тестовый кофе, TST-001`.
5. Жми «🚀 Генерация».
6. n8n шлёт «🚀 Запускаю...».
7. Python шлёт прогресс: «📥 TST-001: исходное фото в S3», «🖼 TST-001: 4/4 фото готовы».
8. В S3-бакете появятся `<batch_id>/TST-001_src.jpg`, `_main.jpg`, `_pack2.jpg`, `_pack3.jpg`, `_extra.jpg`.
9. Бот шлёт «⚠️ Ozon/WB ключи не заданы — пропускаю этапы 2-4».

Логи: `journalctl -u cz-backend -f`.

## 7. Обновление кода

```bash
cd /home/albert/cz-backend
git pull
.venv/bin/pip install -r requirements.txt
sudo systemctl restart cz-backend
journalctl -u cz-backend -f
```

## 8. Откат

Если новая версия не работает:

```bash
sudo systemctl stop cz-backend
cd /home/albert/cz-backend && git checkout <previous-tag>
.venv/bin/pip install -r requirements.txt
sudo systemctl start cz-backend
```

В качестве полного отката можно реактивировать SUB_WF_1/2/3 в n8n и переключить активный workflow.

## 9. Логи и мониторинг

- `journalctl -u cz-backend -f` — Python.
- `docker logs n8n --tail 200 -f` — n8n.
- Telegram-бот `@Content_Zavod_Karusel_bot` — пользовательский интерфейс, прогресс, отчёты.

## 10. Безопасность

- Все секреты — в `/home/albert/cz-backend/.env` mode 0600 owned by albert.
- uvicorn bind 127.0.0.1 — наружу не торчит.
- Опциональный `INTERNAL_TOKEN` для проверки запросов от n8n.
- nginx терминирует TLS перед n8n; Python внутрь не светит.
