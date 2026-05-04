"""Auto-compact: при превышении token cap — сжать старые сообщения в саммари."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import tiktoken

from .llm import KieLLM, LLMError
from .sessions import Session

logger = logging.getLogger(__name__)

# Грубо: используем cl100k_base как универсальный счётчик. Для не-OpenAI моделей
# это всё равно даёт нормальный порядок величины — задача не точная, а
# trigger для compact'а.
try:
    _ENC = tiktoken.get_encoding("cl100k_base")
except Exception:
    _ENC = None


def estimate_tokens(messages: list[dict]) -> int:
    if _ENC is None:
        # Fallback: 1 токен ≈ 4 символа.
        return sum(len(json.dumps(m, ensure_ascii=False)) for m in messages) // 4
    return len(_ENC.encode(json.dumps(messages, ensure_ascii=False)))


def should_compact(messages: list[dict], cap: int) -> bool:
    return estimate_tokens(messages) >= cap


async def compact(sess: Session, llm: KieLLM, *, model: str, archive_dir: Path,
                  keep_tail: int = 4) -> None:
    """Сжимает sess.messages: оставляет последние keep_tail штук + summary как
    первый user-message (Gemini не любит system после старта)."""
    if len(sess.messages) <= keep_tail:
        return
    cutoff = len(sess.messages) - keep_tail
    to_summarize = sess.messages[:cutoff]
    keep = sess.messages[cutoff:]

    summary_text = ""
    try:
        msg = await llm.chat(
            model=model,
            system=(
                "Сожми этот диалог в краткое саммари (~1500 токенов). "
                "Сохрани: цели юзера, ключевые факты, что уже сделано, открытые вопросы. "
                "Markdown без воды."
            ),
            messages=[
                {
                    "role": "user",
                    "content": "ДИАЛОГ ДЛЯ СЖАТИЯ:\n"
                               + json.dumps(to_summarize, ensure_ascii=False)[:60000],
                }
            ],
            tools=None,
            temperature=0.2,
        )
        summary_text = (msg.get("content") or "").strip()
    except LLMError as e:
        logger.warning("compact LLM fail: %s — keep full history", e)
        return

    archive_dir.mkdir(parents=True, exist_ok=True)
    fname = archive_dir / f"{sess.chat_id}_{int(time.time())}.json"
    try:
        fname.write_text(
            json.dumps({"messages": to_summarize, "summary": summary_text},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning("compact archive write fail: %s", e)

    sess.messages = [
        {
            "role": "user",
            "content": f"[АРХИВ ПРЕДЫДУЩЕГО ДИАЛОГА]\n{summary_text}",
        },
        {
            "role": "assistant",
            "content": "Принял, помню контекст. Продолжаем.",
        },
        *keep,
    ]
    logger.info("compact done chat=%s archived=%d kept=%d",
                sess.chat_id, len(to_summarize), len(keep))
