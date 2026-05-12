"""Парсер xlsx-шаблонов Ozon и Wildberries.

Главное API: `parse_template(path) -> TemplateSpec` с авто-детектом
формата.
"""
from .parser import (
    TemplateField,
    TemplateSpec,
    detect_format,
    parse_template,
)

__all__ = [
    "TemplateField",
    "TemplateSpec",
    "detect_format",
    "parse_template",
]
