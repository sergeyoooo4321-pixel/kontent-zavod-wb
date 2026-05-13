# Content Zavod

Telegram bot for preparing marketplace content packs for Ozon and Wildberries.

## Flow

1. User sends product photos one by one.
2. User taps `Готово`.
3. Bot asks for data for each photo in the same order: SKU, title, brand, optional details.
4. Bot generates four marketplace-style images per product:
   `main`, `pack2`, `pack3`, `extra`.
5. Bot uploads images to S3, or to local media fallback if S3 is unavailable.
6. Bot returns a ZIP with:
   - generated photos;
   - public links CSV;
   - Ozon workbook;
   - Wildberries workbook;
   - README for manual import.

The current implementation intentionally does not auto-publish cards to marketplaces. It prepares import workbooks and public image links. This keeps moderation and cabinet-side checks under operator control and avoids invisible/incorrect cards.

## Local Run

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
.venv\Scripts\uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## Server Run

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
sudo cp scripts/cz-backend.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cz-backend
```

## Security

- Do not commit `.env`, snapshots, media, uploads, runtime DBs, or generated ZIPs.
- Telegram webhook can be protected with `TG_WEBHOOK_SECRET_TOKEN`.
- Real API keys are read only from environment variables.
- The bot masks tokens and keys in user-facing errors.
