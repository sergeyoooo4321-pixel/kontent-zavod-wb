"""Tool — абстракция инструмента для tool-loop."""
from __future__ import annotations

from abc import ABC, abstractmethod


class Tool(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    @abstractmethod
    def input_schema(self) -> dict:
        """JSON Schema для аргументов tool."""

    @abstractmethod
    async def run(self, params: dict, ctx) -> dict:
        """Выполнить tool. Возвращает dict; будет JSON-сериализован для LLM."""
