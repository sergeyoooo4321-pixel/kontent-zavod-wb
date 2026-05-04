"""glob — список файлов по паттерну."""
from __future__ import annotations

from pathlib import Path

from .base import Tool


_SKIP_DIRS = {".git", ".venv", ".venv-gnome", "__pycache__", "node_modules", "_external", "data", "sessions"}


class GlobTool(Tool):
    @property
    def name(self) -> str:
        return "glob"

    @property
    def description(self) -> str:
        return (
            "Список файлов в проекте по glob-паттерну (например '**/*.py' или 'app/*.py'). "
            "Возвращает до 100 относительных путей."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob, например '**/*.py'"},
                "path": {"type": "string", "description": "Подпапка от корня workspace"},
                "max_results": {"type": "integer", "default": 100},
            },
            "required": ["pattern"],
        }

    async def run(self, params: dict, ctx) -> dict:
        pattern = params.get("pattern") or ""
        if not pattern:
            return {"ok": False, "error": "pattern пустой"}
        root: Path = ctx.settings.workspace_root
        sub = (params.get("path") or "").strip().lstrip("/\\")
        base = (root / sub).resolve() if sub else root
        try:
            base.relative_to(root)
        except ValueError:
            return {"ok": False, "error": "path вне workspace"}

        max_results = int(params.get("max_results") or 100)
        max_results = max(1, min(max_results, 500))

        out: list[str] = []
        for fp in base.glob(pattern):
            if any(p in _SKIP_DIRS for p in fp.parts):
                continue
            try:
                rel = fp.relative_to(root)
            except ValueError:
                continue
            out.append(str(rel))
            if len(out) >= max_results:
                break
        out.sort()
        return {"ok": True, "files": out, "count": len(out)}
