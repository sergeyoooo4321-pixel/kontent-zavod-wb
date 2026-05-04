from __future__ import annotations
from .base import Tool


class EchoTool(Tool):
    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Возвращает полученный текст без изменений. Smoke-test инструмент."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "Текст для эха"}},
            "required": ["text"],
        }

    async def run(self, params: dict, ctx) -> dict:
        return {"ok": True, "text": params.get("text", "")}
