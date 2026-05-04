# Главный пайплайн (как я работаю)

```
юзер → фото + бренд+артикул+название
       │
       ▼
   generate_image (kie gpt-image-2, 4 варианта)        [requires_approval]
       │
       ▼
   юзер: ✅ Одобряю или ❌ Перегенерить
       │
       ▼
   match_category (категория WB+Ozon)                  [auto]
       │
       ▼
   fill_card (DRY_RUN=true, payload)                   [requires_approval]
       │
       ▼
   юзер: ✅ Одобряю или ❌ Поправить
       │
       ▼
   fill_card (DRY_RUN=false, реальная заливка)         [только после одобрения]
```

## Внешние сервисы за пайплайном

- **kie.ai gpt-image-2** — image-to-image генерация. Модель: `gpt-image-2` через jobs API. Дёргается из cz-backend `app/kie_ai.py` (мы туда ходим через `/internal/generate_image`).
- **S3 (Yandex Cloud)** — хранилище фото. URL'ы публичные, шарим в TG как media_group.
- **WB Content API v2** + **Ozon Seller API** — заливка.

## Ограничения MVP

- `match_category` пока не делает полный матчинг по справочникам — для этого использует старый кнопочный пайплайн «📦 Новая партия».
- `fill_card` с `dry_run=false` пока не реализован — только payload.
- Полная заливка с категориями и атрибутами — через старый сценарий.

Я честно говорю юзеру про эти ограничения если он попросит то что я ещё не умею.
