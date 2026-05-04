"""read_logs — гном читает journalctl своего и основного сервисов."""
from __future__ import annotations

import asyncio

from .base import Tool


_ALLOWED_SERVICES = {"cz-backend", "cz-backend.service", "cz-gnome", "cz-gnome.service"}


class ReadLogsTool(Tool):
    @property
    def name(self) -> str:
        return "read_logs"

    @property
    def description(self) -> str:
        return (
            "Прочитать последние строки journalctl системного сервиса. "
            "Доступно: cz-backend (основной бот), cz-gnome (я сам). "
            "Полезно когда юзер спрашивает 'почему упало', 'что было в логах', и т.п."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "enum": ["cz-backend", "cz-gnome"],
                    "description": "Имя сервиса",
                },
                "lines": {
                    "type": "integer",
                    "description": "Сколько последних строк (default 80, max 400)",
                    "default": 80,
                },
                "grep": {
                    "type": "string",
                    "description": "Опциональный фильтр (подстрока для grep)",
                },
            },
            "required": ["service"],
        }

    async def run(self, params: dict, ctx) -> dict:
        svc = (params.get("service") or "").strip()
        if svc not in _ALLOWED_SERVICES:
            return {"ok": False, "error": f"сервис {svc!r} не разрешён"}
        if not svc.endswith(".service"):
            svc = svc + ".service"
        lines = int(params.get("lines") or 80)
        lines = max(10, min(lines, 400))
        grep = (params.get("grep") or "").strip()

        cmd = ["journalctl", "-u", svc, "-n", str(lines), "--no-pager"]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=20)
        except asyncio.TimeoutError:
            return {"ok": False, "error": "journalctl timeout"}
        except FileNotFoundError:
            return {"ok": False, "error": "journalctl не установлен (не Linux?)"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

        text = stdout.decode("utf-8", errors="replace")
        if proc.returncode != 0 and not text:
            err = stderr.decode("utf-8", errors="replace")
            return {"ok": False, "error": f"journalctl rc={proc.returncode}: {err[:300]}"}

        if grep:
            text = "\n".join(line for line in text.splitlines() if grep.lower() in line.lower())

        # обрезаем для LLM
        if len(text) > 12000:
            text = "...(обрезано)...\n" + text[-12000:]
        return {"ok": True, "service": svc, "lines_returned": len(text.splitlines()), "log": text}
