from __future__ import annotations
from .base import Tool


class ListSkillsTool(Tool):
    @property
    def name(self) -> str:
        return "list_skills"

    @property
    def description(self) -> str:
        return "Возвращает список доступных мне инструментов и скиллов с описаниями."

    @property
    def input_schema(self) -> dict:
        return {"type": "object", "properties": {}}

    async def run(self, params: dict, ctx) -> dict:
        registry = ctx.registry
        items = [
            {"name": t.name, "description": t.description}
            for t in registry.all()
        ]
        return {"ok": True, "tools": items, "count": len(items)}
