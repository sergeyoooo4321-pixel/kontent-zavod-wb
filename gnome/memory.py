"""Memory loader: склейка CLAUDE.md + memory/*.md в системный промпт."""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def build_system_prompt(claude_md: Path, memory_dir: Path, tools_section: str = "") -> str:
    parts: list[str] = []

    if claude_md.exists():
        parts.append(claude_md.read_text("utf-8").strip())
    else:
        logger.warning("CLAUDE.md не найден: %s", claude_md)

    if memory_dir.exists():
        index = memory_dir / "MEMORY.md"
        if index.exists():
            parts.append("\n## Индекс памяти\n" + index.read_text("utf-8").strip())
        for md in sorted(memory_dir.glob("*.md")):
            if md.name == "MEMORY.md":
                continue
            try:
                body = md.read_text("utf-8").strip()
            except Exception as e:
                logger.warning("memory %s read fail: %s", md.name, e)
                continue
            parts.append(f"\n## memory/{md.name}\n{body}")

    if tools_section:
        parts.append("\n## Доступные инструменты\n" + tools_section)

    return "\n\n".join(parts)
