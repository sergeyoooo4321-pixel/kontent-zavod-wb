"""QueryEngine — tool-loop: model → tool_call → tool_result → model → …"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from . import compact, memory
from .config import Settings
from .llm import KieLLM, LLMError
from .sessions import SessionStore
from .tools import ToolRegistry

logger = logging.getLogger(__name__)

# Маркер для approval-flow. Если скилл с requires_approval=true вернул результат —
# tool-loop завершается, а в reply дописывается этот маркер. Bridge парсит и
# рисует inline-кнопки [✅ Одобряю] [❌ Перегенерить].
APPROVAL_MARKER = "[APPROVAL_REQUIRED]"


@dataclass
class ToolCtx:
    """Что инструменты получают как 2-й аргумент run(params, ctx)."""
    settings: Settings
    registry: ToolRegistry
    sessions: SessionStore
    chat_id: int


class QueryEngine:
    def __init__(
        self,
        *,
        settings: Settings,
        llm: KieLLM,
        registry: ToolRegistry,
        sessions: SessionStore,
    ):
        self._cfg = settings
        self._llm = llm
        self._registry = registry
        self._sessions = sessions
        self._system_cache: str | None = None

    def _system_prompt(self) -> str:
        if self._system_cache is None:
            tools_section = "\n".join(
                f"- `{t.name}`: {t.description}" for t in self._registry.all()
            )
            workspace_dir = self._cfg.claude_md.parent
            self._system_cache = memory.build_system_prompt(
                workspace_dir=workspace_dir,
                tools_section=tools_section,
            )
        return self._system_cache

    def reload_memory(self) -> None:
        self._system_cache = None

    async def query(self, chat_id: int, user_text: str, images: list[str] | None = None) -> str:
        lock = self._sessions.lock_for(chat_id)
        async with lock:
            return await self._query_locked(chat_id, user_text, images=images or [])

    async def _query_locked(self, chat_id: int, user_text: str, images: list[str]) -> str:
        sess = self._sessions.load(chat_id)
        # Vision: если есть картинки, формируем content как list (Gemini-стиль)
        if images:
            content = [{"type": "text", "text": user_text}] if user_text else []
            for url in images:
                content.append({"type": "image_url", "image_url": {"url": url}})
                # запоминаем URL чтобы скиллы могли проверить что src_url
                # реальный а не выдуманный LLM
                self._sessions.add_uploaded_url(chat_id, url)
            sess.messages.append({"role": "user", "content": content})
        else:
            sess.messages.append({"role": "user", "content": user_text})

        if compact.should_compact(sess.messages, cap=self._cfg.COMPACT_AT_TOKENS):
            try:
                await compact.compact(
                    sess, self._llm,
                    model=self._cfg.LLM_MODEL,
                    archive_dir=self._cfg.archive_dir,
                )
            except Exception as e:
                logger.warning("compact failed: %s", e)

        system = self._system_prompt()
        tools_schema = self._registry.openai_schemas()
        ctx = ToolCtx(
            settings=self._cfg,
            registry=self._registry,
            sessions=self._sessions,
            chat_id=chat_id,
        )

        for step in range(self._cfg.MAX_STEPS):
            try:
                msg = await self._llm.chat(
                    model=self._cfg.LLM_MODEL,
                    system=system,
                    messages=sess.messages,
                    tools=tools_schema,
                )
            except LLMError as e:
                logger.warning("LLM error on %s: %s — try fallback %s",
                               self._cfg.LLM_MODEL, e, self._cfg.LLM_FALLBACK_MODEL)
                if self._cfg.LLM_FALLBACK_MODEL and self._cfg.LLM_FALLBACK_MODEL != self._cfg.LLM_MODEL:
                    try:
                        msg = await self._llm.chat(
                            model=self._cfg.LLM_FALLBACK_MODEL,
                            system=system,
                            messages=sess.messages,
                            tools=tools_schema,
                        )
                    except LLMError as e2:
                        self._sessions.save(sess)
                        return f"[ошибка LLM]: {e2}"
                else:
                    self._sessions.save(sess)
                    return f"[ошибка LLM]: {e}"

            sess.messages.append(_clean_assistant(msg))

            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                self._sessions.save(sess)
                return (msg.get("content") or "").strip() or "[пустой ответ]"

            # Выполнить все tool_calls и добавить tool-результаты в историю
            approval_needed = False
            for call in tool_calls:
                fn = call.get("function") or {}
                name = fn.get("name") or ""
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except Exception:
                    args = {}
                tool = self._registry.get(name)
                if tool is None:
                    result = {"ok": False, "error": f"tool '{name}' не найден"}
                else:
                    try:
                        result = await tool.run(args, ctx)
                    except Exception as e:
                        logger.exception("tool %s failed", name)
                        result = {"ok": False, "error": str(e)}
                    # Если у tool помечено requires_approval — после его
                    # выполнения мы не идём в следующий шаг loop, а просим
                    # ассистента сформировать reply с маркером.
                    if getattr(tool, "requires_approval", False):
                        approval_needed = True
                sess.messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "name": name,
                    "content": json.dumps(result, ensure_ascii=False)[:8000],
                })

            if approval_needed:
                # Делаем ОДИН финальный вызов LLM чтобы он сформулировал
                # reply юзеру с отчётом + вопросом «одобряешь?»
                try:
                    msg = await self._llm.chat(
                        model=self._cfg.LLM_MODEL,
                        system=system + "\n\nВАЖНО: только что отработал скилл с "
                                        "approval-флагом. Покажи юзеру результат и "
                                        "явно спроси одобрения. НЕ вызывай больше tools.",
                        messages=sess.messages,
                        tools=None,
                        temperature=0.2,
                    )
                    sess.messages.append(_clean_assistant(msg))
                    self._sessions.save(sess)
                    text = (msg.get("content") or "").strip() or "Одобряешь?"
                    return f"{text}\n\n{APPROVAL_MARKER}"
                except LLMError as e:
                    self._sessions.save(sess)
                    return f"[ошибка финализации approval]: {e}\n\n{APPROVAL_MARKER}"

        self._sessions.save(sess)
        return "[предел шагов tool-loop, остановился]"


def _clean_assistant(msg: dict) -> dict:
    """Оставляем только role/content/tool_calls — без лишних полей провайдера."""
    out: dict = {"role": "assistant", "content": msg.get("content") or ""}
    if msg.get("tool_calls"):
        out["tool_calls"] = msg["tool_calls"]
    return out
