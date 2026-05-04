# Гномик 🧙

24/7 AI-агент-резидент проекта «Контент-завод». Живёт в `gnome/` рядом с основным ботом (`app/`), запускается как отдельный systemd-юнит `cz-gnome.service` на порту 8001.

## Что умеет (Этап 1)

- Tool-loop через kie.ai (модель `gemini-3-pro` по умолчанию).
- Сессии per `chat_id` в SQLite — переживают рестарт сервиса.
- Память: `CLAUDE.md` + `memory/*.md` склеиваются в системный промпт при первом запросе.
- Auto-compact: при превышении ~80K токенов сессия автоматически сжимается, старая часть архивируется в `sessions/archive/`.
- Скиллы — формат как у openclaw (`skills/<name>/{manifest.yaml, skill.py}`); реальные скиллы под МП — в Этапе 2.

## Базовые встроенные tools

- `echo` — smoke-test
- `list_skills` — вернёт список всех инструментов
- `read_memory` — прочитать `CLAUDE.md` или `memory/<name>.md`

## Локальный запуск

```bash
cd kontent-zavod-wb/
python -m venv .venv-gnome
.venv-gnome/Scripts/pip install -r gnome/requirements.txt   # Windows
# или
.venv-gnome/bin/pip install -r gnome/requirements.txt        # Linux

cp gnome/.env.example gnome/.env  # заполнить KIE_API_KEY
.venv-gnome/bin/uvicorn gnome.main:app --port 8001 --reload
```

## API

- `GET /healthz` — статус, модель, список tools.
- `POST /chat` — `{"chat_id": 1, "text": "..."}` → `{"reply": "..."}`.
- `GET /sessions` — список активных chat_id.
- `POST /sessions/{chat_id}/reset` — очистить историю чата.
- `POST /reload-memory` — перечитать `CLAUDE.md` + `memory/*.md` без рестарта.

## Smoke-test

```bash
curl -s http://127.0.0.1:8001/healthz
curl -s -X POST http://127.0.0.1:8001/chat \
  -H "Content-Type: application/json" \
  -d '{"chat_id":1,"text":"Привет, кто ты?"}'
curl -s -X POST http://127.0.0.1:8001/chat \
  -d '{"chat_id":1,"text":"Какие у тебя инструменты?"}'
curl -s -X POST http://127.0.0.1:8001/chat \
  -d '{"chat_id":1,"text":"Что я тебя спрашивал в первом сообщении?"}'  # должен вспомнить
```

## Деплой

```bash
python deploy_gnome.py    # из родительской папки (Новая папка (2)/)
```

Скрипт делает: `git pull` → создаёт venv `.venv-gnome` → `pip install` → копирует systemd-юнит → restart `cz-gnome.service` → проверяет healthz.

## Этап 2 (готово)

- ✅ Workspace: `workspace.yaml` + `BOOTSTRAP.md` + расширенные `CLAUDE.md` / `memory/`.
- ✅ Vision-input: `/chat` принимает `images: list[str]` (URLs).
- ✅ TG-bridge через существующий бот (кнопка «🎨 Гном-генерация» + фаза `gnome_chat`).
- ✅ 3 скилла под МП: `generate_image`, `match_category`, `fill_card` (обёртки над cz-backend `/internal/*`).
- ✅ Approval-flow: маркер `[APPROVAL_REQUIRED]` + inline-кнопки `✅ Одобряю / ❌ Перегенерить`.

## Этап 3 (TODO)

- Полный категорийный матчинг через гнома (сейчас MVP).
- Реальная заливка через `fill_card(dry_run=false)` (сейчас только payload).
- Slash-команды `/compact /clear /skills /map`.
- Sub-agents для долгих фоновых задач.
- Self-check / heartbeat.

## Источники архитектуры

См. [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) — взято от openclaude (MIT) и openclaw (MIT).
