# Skills

Папка для скиллов гномика. Каждый скилл — подпапка с двумя файлами:

```
skills/
└── my_skill/
    ├── manifest.yaml
    └── skill.py
```

## manifest.yaml

```yaml
name: my_skill
description: "Что делает скилл — увидит LLM, по этой строке решает звать или нет."
version: 0.1.0
input_schema:
  type: object
  properties:
    arg1: { type: string }
  required: [arg1]
permissions: []
```

## skill.py

```python
async def run(params: dict, ctx) -> dict:
    """ctx — ToolCtx (settings, registry, sessions, chat_id)."""
    return {"ok": True, "content": "..."}
```

`ctx.settings` — `gnome.config.Settings`, `ctx.registry` — реестр всех tools/skills, `ctx.sessions` — SessionStore (если нужно почитать/записать чужую сессию), `ctx.chat_id` — id текущего чата.

Скиллы автоматически подхватываются при старте сервиса. Чтобы перечитать без рестарта — пока никак, нужен restart (в Этапе 2 добавим горячую перезагрузку).
