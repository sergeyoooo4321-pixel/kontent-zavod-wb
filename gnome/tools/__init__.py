"""Реестр инструментов агента — builtin'ы + auto-discover скиллов из skills/."""
from __future__ import annotations

import importlib.util
import logging
from pathlib import Path

import yaml

from .base import Tool

logger = logging.getLogger(__name__)


def _load_builtin() -> list[Tool]:
    from .echo import EchoTool
    from .list_skills_tool import ListSkillsTool
    from .read_memory import ReadMemoryTool
    from .file_read import FileReadTool
    from .grep_tool import GrepTool
    from .glob_tool import GlobTool
    from .read_logs import ReadLogsTool
    from .list_dir import ListDirTool
    return [
        EchoTool(),
        ListSkillsTool(),
        ReadMemoryTool(),
        FileReadTool(),
        GrepTool(),
        GlobTool(),
        ReadLogsTool(),
        ListDirTool(),
    ]


def _load_skills(skills_dir: Path) -> list[Tool]:
    """Сканирует skills/<name>/ — каждая папка с manifest.yaml + skill.py становится Tool."""
    out: list[Tool] = []
    if not skills_dir.exists():
        return out
    for skill_dir in skills_dir.iterdir():
        if not skill_dir.is_dir():
            continue
        manifest = skill_dir / "manifest.yaml"
        skill_py = skill_dir / "skill.py"
        if not (manifest.exists() and skill_py.exists()):
            continue
        try:
            meta = yaml.safe_load(manifest.read_text("utf-8")) or {}
            spec = importlib.util.spec_from_file_location(
                f"gnome.skills.{skill_dir.name}", skill_py
            )
            mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            run = getattr(mod, "run", None)
            if not callable(run):
                logger.warning("skill %s: нет async def run(params, ctx)", skill_dir.name)
                continue
            out.append(_SkillTool(meta=meta, run=run, skill_dir=skill_dir))
            logger.info("loaded skill: %s", meta.get("name") or skill_dir.name)
        except Exception as e:
            logger.warning("skill %s load fail: %s", skill_dir.name, e)
    return out


class _SkillTool(Tool):
    def __init__(self, *, meta: dict, run, skill_dir: Path):
        self._meta = meta
        self._run = run
        self._dir = skill_dir
        self.requires_approval = bool(meta.get("requires_approval", False))

    @property
    def name(self) -> str:
        return self._meta.get("name") or self._dir.name

    @property
    def description(self) -> str:
        return self._meta.get("description") or ""

    @property
    def input_schema(self) -> dict:
        return self._meta.get("input_schema") or {"type": "object", "properties": {}}

    async def run(self, params: dict, ctx) -> dict:  # type: ignore[override]
        return await self._run(params, ctx)


class ToolRegistry:
    def __init__(self, skills_dir: Path):
        self._tools: dict[str, Tool] = {}
        for t in _load_builtin():
            self._tools[t.name] = t
        for t in _load_skills(skills_dir):
            self._tools[t.name] = t

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def openai_schemas(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
            for t in self._tools.values()
        ]
