# Главный пайплайн (ZIP-фабрика)

```
юзер → фото + «артикул, бренд - название» (по каждому товару)
       ↓
юзер: «поехали» (после сбора партии)
       ↓
✋ approval-точка: я подытоживаю и спрашиваю «поехали?»
       ↓
юзер: «✅»
       ↓
make_batch_zip(products, cabinet?)
       │
       ├─ фото: 4 шт на товар через gpt-image-2 image-to-image (3:4)
       ├─ категории: Ozon + WB через LLM
       ├─ lookup в кеше templates/<cabinet>/<mp>/<category_id>/
       │       └─ если нет — пайплайн просит юзера скинуть пустой xlsx
       ├─ заполнение xlsx по правилам ТЗ §5.2 (SKU x1/x2/x3, имена,
       │   габариты+1, веса, мультивыбор `;`, справочники из перечня)
       ├─ сборка ZIP: photos/+ozon/+wb/+README.txt
       └─ tg.send_document юзеру
       ↓
Юзер скачивает ZIP, распаковывает, грузит xlsx в свои кабинеты
МП через стандартную массовую загрузку.
```

## Внешние сервисы

- **aitunnel.ru** (OpenAI-совместимый агрегатор): LLM `gemini-3.1-pro-preview` + fallback `claude-sonnet-4.6`, image-to-image `gpt-image-2` через `/v1/images/edits`.
- **Yandex Object Storage**: фото в S3 с публичными URL'ами; URL'ы попадают в xlsx-колонки фото.
- **Wildberries Content API v2**: `subject_charcs` + `directory_values` — подгрузка значений выпадающих списков WB-шаблонов.
- **Ozon Seller API**: используется только для `category_tree` (определение категории). Заливка карточек НЕ через API — через xlsx, который грузит юзер.

## Что я НЕ делаю

- Не публикую через `/v3/product/import` (Ozon) или `/content/v2/cards/upload` (WB).
- Не отправляю фото в МП отдельно — они подтягиваются по URL'ам из xlsx.
- Не выдумываю значения справочников. Беру строго из `validation` (Ozon) или `directory_values` (WB).
- Не собираю WB-шаблон «с нуля» — использую пустой xlsx от продавца (один раз скидывает на категорию).
