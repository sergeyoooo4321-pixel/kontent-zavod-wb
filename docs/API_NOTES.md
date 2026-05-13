# API Notes

Checked on 2026-05-13 before rebuild.

## Telegram

Official Bot API supports receiving photos/documents through updates, calling `getFile`, then downloading bytes by `file_path`. Bot API file download links are temporary, so Content Zavod stores the final generated media in S3/local media, not in Telegram.

Source: https://core.telegram.org/bots/api

## Ozon

Ozon Seller API supports product upload through `POST /v2/product/import`; upload is asynchronous and moderation may take days. Ozon requires real category/attribute data and supports checking import status through `POST /v1/product/import/info`.

Ozon category selection is not optional: the category/type defines the set of product characteristics and a wrong category can fail moderation. XLS upload templates are downloaded in the seller account after choosing the category/type.

Decision: the app now resolves category/type through Seller API dictionaries, records required attributes and allowed values, and fills an official XLSX template only when that category template is cached. If the template is missing, the ZIP includes `missing_templates.md` instead of pretending the generic workbook is upload-ready. Direct API publication should be enabled only after attribute mapping is verified per cabinet.

Sources:
- https://docs.ozon.com/global/api/via-api/
- https://docs.ozon.com/global/products/requirements/product-info/category/

## Wildberries

WB Content API supports categories/subjects/characteristics and card creation through `POST /content/v2/cards/upload`. Media can be uploaded by file or by public links; image links must be direct and unauthenticated. WB dimensions are centimeters, packed weight is kilograms.

The current WB Content API exposes subject lookup through `/content/v2/object/all`, subject characteristics through `/content/v2/object/charcs/{subjectId}`, and standard directories such as colors, gender, countries, seasons, VAT and HS-codes.

Decision: the app now resolves WB subject IDs, reads characteristics and standard directories, fills allowed values where it can, and reports missing required fields. Direct API publication can be added later using the same normalized product model.

Source: https://dev.wildberries.ru/en/docs/openapi/work-with-products

## Yandex Object Storage

Yandex Object Storage is S3-compatible. The app writes generated media through S3 API when credentials are configured and falls back to local media when S3 rejects writes.

Source: https://yandex.cloud/en/docs/storage/s3/

## Image Generation

The app calls an OpenAI-compatible `/images/edits` endpoint through Aitunnel settings. If the provider fails, a deterministic Pillow fallback image is generated so the batch still completes and the operator receives a usable ZIP.
