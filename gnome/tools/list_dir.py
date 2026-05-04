"""list_dir — содержимое папки workspace."""
from __future__ import annotations

from pathlib import Path

from .base import Tool


_SKIP = {".git", ".venv", ".venv-gnome", "__pycache__", "node_modules", "_external"}


class ListDirTool(Tool):
    @property
    def name(self) -> str:
        return "list_dir"

    @property
    def description(self) -> str:
        return "Содержимое папки в проекте — имена файлов и подпапок. Полезно чтобы понять структуру."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Относительный путь, '' = корень"},
            },
            "required": [],
        }

    async def run(self, params: dict, ctx) -> dict:
        root: Path = ctx.settings.workspace_root
        sub = (params.get("path") or "").strip().lstrip("/\\")
        target = (root / sub).resolve() if sub else root
        try:
            target.relative_to(root)
        except ValueError:
            return {"ok": False, "error": "path вне workspace"}
        if not target.exists():
            return {"ok": False, "error": "не найдено"}
        if not target.is_dir():
            return {"ok": False, "error": "это файл, используй file_read"}
        items = []
        for child in sorted(target.iterdir()):
            if child.name in _SKIP:
                continue
            kind = "dir" if child.is_dir() else "file"
            try:
                size = child.stat().st_size if kind == "file" else None
            except Exception:
                size = None
            items.append({"name": child.name, "kind": kind, "size": size})
        return {"ok": True, "path": sub or ".", "items": items}
