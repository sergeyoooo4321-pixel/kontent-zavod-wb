"""Парсер xlsx-шаблонов Ozon и Wildberries.

Главные API:
    parse_template(path) -> TemplateSpec
        универсальный парсер с авто-детектом формата

    OzonTemplate (из legacy.py)
        старый класс заполнения существующего Ozon-шаблона
"""
from .legacy import OzonTemplate
from .parser import (
    TemplateField,
    TemplateSpec,
    detect_format,
    parse_template,
)

__all__ = [
    "OzonTemplate",
    "TemplateField",
    "TemplateSpec",
    "detect_format",
    "parse_template",
]
