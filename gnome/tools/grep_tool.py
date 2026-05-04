"""grep — поиск по тексту в файлах workspace."""
from __future__ import annotations

import re
from pathlib import Path

from .base import Tool


_DEFAULT_GLOBS = ["**/*.py", "**/*.md", "**/*.yaml", "**/*.yml", "**/*.toml"]
_SKIP_DIRS = {".git", ".venv", ".venv-gnome", "__pycache__", "node_modules", "_external", "data", "sessions"}


class GrepTool(Tool):
    @property
    def name(self) -> str:
        return "grep"

    @property
    def description(self) -> str:
        return (
            "Поиск регулярки по содержимому файлов проекта. По умолчанию ищет по "
            "*.py / *.md / *.yaml. Возвращает до 30 совпадений в формате "
            "'path:line: match'."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Регулярное выражение"},
                "path": {
                    "type": "string",
                    "description": "Подпапка относительно workspace (например 'app/' или 'gnome/'). По умолчанию весь workspace.",
                },
                "globs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Список glob-паттернов для файлов (по умолчанию py/md/yaml)",
                },
                "max_matches": {"type": "integer", "default": 30},
            },
            "required": ["pattern"],
        }

    async def run(self, params: dict, ctx) -> dict:
        pattern = params.get("pattern") or ""
        if not pattern:
            return {"ok": False, "error": "pattern пустой"}
        try:
            rx = re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            return {"ok": False, "error": f"плохая регулярка: {e}"}

        root: Path = ctx.settings.workspace_root
        sub = (params.get("path") or "").strip().lstrip("/\\")
        base = (root / sub).resolve() if sub else root
        try:
            base.relative_to(root)
        except ValueError:
            return {"ok": False, "error": "path вне workspace"}
        if not base.exists():
            return {"ok": False, "error": "путь не найден"}

        globs = params.get("globs") or _DEFAULT_GLOBS
        max_matches = int(params.get("max_matches") or 30)
        max_matches = max(1, min(max_matches, 100))

        matches: list[str] = []
        seen_files: set[Path] = set()
        for g in globs:
            for fp in base.glob(g):
                if fp in seen_files:
                    continue
                seen_files.add(fp)
                if not fp.is_file():
                    continue
                if any(p in _SKIP_DIRS for p in fp.parts):
                    continue
                try:
                    text = fp.read_text("utf-8", errors="replace")
                except Exception:
                    continue
                for ln, line in enumerate(text.splitlines(), start=1):
                    if rx.search(line):
                        rel = fp.relative_to(root)
                        snippet = line.strip()[:200]
                        matches.append(f"{rel}:{ln}: {snippet}")
                        if len(matches) >= max_matches:
                            return {"ok": True, "matches": matches, "truncated": True}
        return {"ok": True, "matches": matches, "truncated": False}
