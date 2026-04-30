# Деплой «Контент завод»

Целевой сервер: **Ubuntu 24.04** + Python 3.12 + nginx + Let's Encrypt. **Никаких Docker-контейнеров не требуется.**

## 1. Требования

| Компонент | Версия |
|---|---|
| OS | Ubuntu 24.04+ |
| Python | 3.12+ |
| nginx | для TLS терминации |
| certbot | для автоматического Let's Encrypt |

## 2. Yandex Object Storage

Создать сервисный аккаунт с ролью `storage.editor` на нужный folder, сгенерировать **HMAC-ключи** (Access Key ID + Secret).

Создать бакет (один раз, например через `aws s3api`):

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
git clone https://github.com/sergeyoooo4321-pixel/kontent-zavod-wb /home/albert/cz-backend

# 3. виртуальное окружение
cd /home/albert/cz-backend
python3.12 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

# 4. .env (НЕ коммитим!)
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

## 4. nginx

`/etc/nginx/sites-available/contentzavodprofit.ru`:

```nginx
server {
    listen 80;
    server_name contentzavodprofit.ru;
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl;
    server_name contentzavodprofit.ru;

    ssl_certificate /etc/letsencrypt/live/contentzavodprofit.ru/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/contentzavodprofit.ru/privkey.pem;

    location ^~ /.well-known/acme-challenge/ {
        root /var/www/letsencrypt;
        default_type text/plain;
    }

    # Telegram webhook → Python
    location /tg/ {
        proxy_pass http://127.0.0.1:8000/tg/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location = /healthz {
        proxy_pass http://127.0.0.1:8000/healthz;
    }

    location / {
        return 200 "Контент завод backend. See /healthz.\n";
        add_header Content-Type text/plain;
    }
}
```

Активация:
```bash
sudo ln -sf /etc/nginx/sites-available/contentzavodprofit.ru /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

## 5. ufw (firewall)

```bash
sudo ufw allow 22022/tcp     # SSH
sudo ufw allow 80/tcp        # HTTP (для certbot)
sudo ufw allow 443/tcp       # HTTPS

# Бэкенд (порт 8000) — только loopback
sudo ufw allow from 127.0.0.0/8 to any port 8000 proto tcp

sudo ufw enable
```

## 6. Telegram setWebhook

```bash
curl -X POST "https://api.telegram.org/bot${TG_BOT_TOKEN}/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://contentzavodprofit.ru/tg/webhook","drop_pending_updates":true}'

# Проверка
curl "https://api.telegram.org/bot${TG_BOT_TOKEN}/getWebhookInfo"
```

## 7. Smoke-тест

Открой `@Content_Zavod_Karusel_bot` в Telegram, нажми `/start`. Должен ответить с приветствием и клавиатурой.

Для теста пайплайна без Telegram:
```bash
curl -X POST http://127.0.0.1:8000/api/run \
  -H "Content-Type: application/json" \
  -d '{"batch_id":"test-001","chat_id":123,"products":[{"idx":0,"sku":"TST-1","name":"Тест","tg_file_id":"AgACAgIAA-FAKE-LONG-FILE-ID"}]}'
```

## 8. Обновление

```bash
cd /home/albert/cz-backend
git pull
.venv/bin/pip install -r requirements.txt
sudo systemctl restart cz-backend
journalctl -u cz-backend -f
```

## 9. Логи и мониторинг

- Бэкенд: `journalctl -u cz-backend -f`
- nginx: `sudo tail -f /var/log/nginx/access.log` и `error.log`
- Telegram-бот: пользовательский интерфейс прогресса/ошибок

## 10. Откат

Безопасный откат к предыдущей версии:
```bash
cd /home/albert/cz-backend
git log --oneline -10
git checkout <previous-commit>
sudo systemctl restart cz-backend
```
