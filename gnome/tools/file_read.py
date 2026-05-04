"""file_read — гном читает файлы внутри workspace_root."""
from __future__ import annotations

from pathlib import Path

from .base import Tool


# Чёрный список: куда нельзя ходить даже внутри корня
_DENY_NAMES = {".env", ".git", "__pycache__", "node_modules", ".venv", ".venv-gnome"}
_DENY_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".pyc", ".jpg", ".jpeg", ".png", ".webp", ".zip"}


class FileReadTool(Tool):
    @property
    def name(self) -> str:
        return "file_read"

    @property
    def description(self) -> str:
        return (
            "Прочитать файл из проекта (репо контент-завода). Путь относительно "
            "корня проекта (WORKSPACE_ROOT). Запрещены: .env, .git, бинарники, БД. "
            "Для логов используй read_logs, для поиска по содержимому — grep."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Относительный путь от корня репо, например 'app/pipeline.py' или 'gnome/CLAUDE.md'",
                },
                "max_bytes": {
                    "type": "integer",
                    "description": "Максимум байт для возврата (по умолчанию 12000)",
                    "default": 12000,
                },
            },
            "required": ["path"],
        }

    async def run(self, params: dict, ctx) -> dict:
        rel = (params.get("path") or "").strip().lstrip("/\\")
        if not rel:
            return {"ok": False, "error": "path пустой"}
        max_bytes = int(params.get("max_bytes") or 12000)
        max_bytes = min(max(1024, max_bytes), 60000)

        root: Path = ctx.settings.workspace_root
        target = (root / rel).resolve()
        # whitelist: путь должен лежать ВНУТРИ root
        try:
            target.relative_to(root)
        except ValueError:
            return {"ok": False, "error": f"путь вне workspace: {rel}"}
        # blacklist
        for part in target.parts:
            if part in _DENY_NAMES:
                return {"ok": False, "error": f"запрещённый путь (содержит {part})"}
        if target.suffix.lower() in _DENY_SUFFIXES:
            return {"ok": False, "error": f"запрещённый тип файла {target.suffix}"}
        if not target.exists():
            return {"ok": False, "error": "файл не найден"}
        if target.is_dir():
            return {"ok": False, "error": "это директория, используй list_dir"}
        try:
            data = target.read_bytes()
        except Exception as e:
            return {"ok": False, "error": str(e)}
        truncated = len(data) > max_bytes
        body = data[:max_bytes].decode("utf-8", errors="replace")
        return {
            "ok": True,
            "path": rel,
            "size": len(data),
            "truncated": truncated,
            "content": body,
        }
