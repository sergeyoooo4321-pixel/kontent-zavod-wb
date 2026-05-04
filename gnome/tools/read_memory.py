from __future__ import annotations
from pathlib import Path
from .base import Tool


class ReadMemoryTool(Tool):
    @property
    def name(self) -> str:
        return "read_memory"

    @property
    def description(self) -> str:
        return (
            "Читает файл из папки memory/ или CLAUDE.md. "
            "Параметр name = имя без расширения (например 'identity'), "
            "или 'CLAUDE' для CLAUDE.md."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Имя файла без .md, либо 'CLAUDE' для главного промпта",
                },
            },
            "required": ["name"],
        }

    async def run(self, params: dict, ctx) -> dict:
        name = (params.get("name") or "").strip()
        if not name:
            return {"ok": False, "error": "name пустой"}
        if name.upper() == "CLAUDE":
            path: Path = ctx.settings.claude_md
        else:
            safe = name.replace("/", "").replace("..", "").replace("\\", "")
            path = ctx.settings.memory_dir / f"{safe}.md"
        if not path.exists():
            return {"ok": False, "error": f"файл не найден: {path.name}"}
        try:
            content = path.read_text("utf-8")
        except Exception as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "name": name, "content": content[:8000]}
