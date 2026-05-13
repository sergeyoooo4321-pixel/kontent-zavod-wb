#!/usr/bin/env bash
set -euo pipefail

cd /home/albert/cz-backend
git pull --ff-only origin main
python3.12 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
sudo cp scripts/cz-backend.service /etc/systemd/system/cz-backend.service
sudo systemctl daemon-reload
sudo systemctl enable --now cz-backend.service
sudo systemctl restart cz-backend.service
systemctl status cz-backend.service --no-pager --lines=30
