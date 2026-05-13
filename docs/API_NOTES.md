# API Notes

Checked on 2026-05-13 before rebuild.

## Telegram

Official Bot API supports receiving photos/documents through updates, calling `getFile`, then downloading bytes by `file_path`. Bot API file download links are temporary, so Content Zavod stores the final generated media in S3/local media, not in Telegram.

Source: https://core.telegram.org/bots/api

## Ozon

Ozon Seller API supports product upload through `POST /v2/product/import`; upload is asynchronous and moderation may take days. Ozon requires real category/attribute data and supports checking import status through `POST /v1/product/import/info`.

Decision: current rebuild prepares an Ozon workbook and public image links first. Direct API publication should be enabled only after category/attribute mapping is verified per cabinet.

Source: https://docs.ozon.com/global/api/via-api/

## Wildberries

WB Content API supports categories/subjects/characteristics and card creation through `POST /content/v2/cards/upload`. Media can be uploaded by file or by public links; image links must be direct and unauthenticated. WB dimensions are centimeters, packed weight is kilograms.

Decision: current rebuild prepares a WB workbook and public image links first. Direct API publication can be added later using the same normalized product model.

Source: https://dev.wildberries.ru/en/docs/openapi/work-with-products

## Yandex Object Storage

Yandex Object Storage is S3-compatible. The app writes generated media through S3 API when credentials are configured and falls back to local media when S3 rejects writes.

Source: https://yandex.cloud/en/docs/storage/s3/

## Image Generation

The app calls an OpenAI-compatible `/images/edits` endpoint through Aitunnel settings. If the provider fails, a deterministic Pillow fallback image is generated so the batch still completes and the operator receives a usable ZIP.

