#!/usr/bin/env bash
# Идемпотентный деплой Python-бэкенда на сервер.
# Запускать на сервере от пользователя albert (или передавать через ssh).
set -euo pipefail

REPO_DIR="${REPO_DIR:-/home/albert/cz-backend}"
SERVICE_NAME="cz-backend"

echo "[deploy] target=$REPO_DIR"

if [ ! -d "$REPO_DIR" ]; then
    echo "[deploy] cloning repo..."
    git clone https://github.com/sergeyoooo4321-pixel/kontent-zavod-wb "$REPO_DIR"
fi

cd "$REPO_DIR"

echo "[deploy] git pull..."
git pull --ff-only

echo "[deploy] venv + deps..."
if [ ! -d ".venv" ]; then
    python3.12 -m venv .venv
fi
.venv/bin/pip install --upgrade pip --quiet
.venv/bin/pip install -r requirements.txt --quiet

if [ ! -f ".env" ]; then
    echo "[deploy] WARNING: .env not found. Copying .env.example — заполни секреты!"
    cp .env.example .env
    chmod 0600 .env
fi

echo "[deploy] installing systemd unit..."
sudo cp scripts/cz-backend.service /etc/systemd/system/$SERVICE_NAME.service
sudo systemctl daemon-reload
sudo systemctl enable $SERVICE_NAME
sudo systemctl restart $SERVICE_NAME

echo "[deploy] waiting healthz..."
for i in {1..20}; do
    if curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1; then
        echo "[deploy] OK"
        curl -s http://127.0.0.1:8000/healthz
        exit 0
    fi
    sleep 1
done
echo "[deploy] FAILED — see: journalctl -u $SERVICE_NAME -n 50"
exit 1
