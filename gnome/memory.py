"""Memory loader: workspace.yaml + bootstrap → системный промпт."""
from __future__ import annotations

import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


def load_workspace(workspace_dir: Path) -> dict:
    """Читает gnome/workspace.yaml. Если нет — возвращает пустой dict."""
    wf = workspace_dir / "workspace.yaml"
    if not wf.exists():
        return {}
    try:
        return yaml.safe_load(wf.read_text("utf-8")) or {}
    except Exception as e:
        logger.warning("workspace.yaml read fail: %s", e)
        return {}


def build_system_prompt(workspace_dir: Path, tools_section: str = "") -> str:
    """Собирает system prompt: workspace meta → bootstrap-файлы → tools.

    Bootstrap-список берётся из workspace.yaml. Если его нет — фолбэк
    на CLAUDE.md + memory/*.md (старое поведение).
    """
    parts: list[str] = []

    ws = load_workspace(workspace_dir)
    if ws:
        parts.append(
            f"# Workspace: {ws.get('name', '?')}\n"
            f"Owner: {ws.get('owner', '?')}\n"
            f"Purpose: {ws.get('purpose', '').strip()}"
        )

    bootstrap_files: list[str] = ws.get("bootstrap") if isinstance(ws.get("bootstrap"), list) else []
    if not bootstrap_files:
        # фолбэк: всё что было раньше
        bootstrap_files = ["CLAUDE.md"]
        memory_dir = workspace_dir / "memory"
        if memory_dir.exists():
            for md in sorted(memory_dir.glob("*.md")):
                if md.name != "MEMORY.md":
                    bootstrap_files.append(f"memory/{md.name}")

    for rel in bootstrap_files:
        path = workspace_dir / rel
        if not path.exists():
            logger.warning("bootstrap file %s не найден", rel)
            continue
        try:
            body = path.read_text("utf-8").strip()
        except Exception as e:
            logger.warning("bootstrap %s read fail: %s", rel, e)
            continue
        parts.append(f"## {rel}\n{body}")

    if tools_section:
        parts.append("## Доступные инструменты\n" + tools_section)

    return "\n\n".join(parts)
