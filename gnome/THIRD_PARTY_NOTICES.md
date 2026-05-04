# Third-party notices

Архитектура и часть концепций гнома вдохновлены двумя open-source проектами под лицензией MIT:

- **openclaude** — https://github.com/Gitlawb/openclaude (MIT)
  - Tool-loop pattern (модель → tool_use → tool_result → модель), концепция Tool с описанием и schema.
  - Структура system prompt: воркспейс / задача / память / описание инструментов.

- **openclaw** — https://github.com/openclaw/openclaw (MIT)
  - Концепция Workspace как папки агента (`workspace.yaml`, bootstrap-файлы).
  - Формат скилла: папка с `manifest.yaml` + кодом и автодискавер на старте.
  - Approval-flow для tools, изменяющих состояние внешних систем.

Кода ни строчки не скопировано дословно — оба проекта на TypeScript/Node, у нас Python/FastAPI.
Идеи и архитектура переосмыслены под задачу автозаливки карточек на WB/Ozon.
