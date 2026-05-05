"""Pydantic-модели для входящих запросов и внутреннего состояния пайплайна."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ─── входящие из n8n ──────────────────────────────────────────────


class ProductIn(BaseModel):
    idx: int
    sku: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=500)
    # Один из двух обязателен:
    #   tg_file_id — pipeline сам скачает фото из TG и зальёт в S3 (старый flow)
    #   src_url    — фото уже в S3, скачивание пропускается (для гном-flow)
    tg_file_id: str = ""
    src_url: str | None = None
    brand: str | None = None
    # опциональные данные товара (если есть — используются; иначе LLM-fallback или дефолты)
    weight_g: int | None = Field(default=None, ge=1, le=1_000_000)
    dims: dict[str, float] | None = None  # {"l":..,"w":..,"h":..}


class RunRequest(BaseModel):
    batch_id: str
    chat_id: int
    products: list[ProductIn] = Field(min_length=1, max_length=10)
    # Cabinet routing: список имён кабинетов из settings.list_cabinets().
    # None / пусто → используем default_cabinet_name (backward-compat).
    # Несколько имён → mirror-режим: одна и та же карточка во все указанные кабинеты.
    cabinet_names: list[str] | None = None


class RunResponse(BaseModel):
    batch_id: str
    queued: bool
    received_at: str


# ─── внутреннее состояние пайплайна ─────────────────────────────────


class CategoryRef(BaseModel):
    id: int
    type_id: int | None = None
    path: str = ""
    score: float = 1.0


class ProductState(BaseModel):
    """Состояние одного товара в процессе обработки. Накапливаем по этапам."""

    idx: int
    sku: str
    name: str
    tg_file_id: str
    brand: str | None = None

    # Этап 1
    src_url: str | None = None
    images: dict[str, str] = Field(default_factory=dict)  # {"main": url, ...}

    # Этап 2
    ozon_category: CategoryRef | None = None
    wb_subject: CategoryRef | None = None

    # Этап 3
    skus_3: list[dict[str, Any]] = Field(default_factory=list)  # [{sku, qty, ...}]
    titles: dict[str, dict[str, str]] = Field(default_factory=dict)  # per sku_x{1,2,3}
    # Маппинг атрибутов/характеристик. None как значение = required не нашёлся, SKU исключаем.
    attributes_ozon: dict[str, list[dict] | None] = Field(default_factory=dict)
    characteristics_wb: dict[str, list[dict] | None] = Field(default_factory=dict)

    # Этап 4
    ozon_status: dict[str, str] = Field(default_factory=dict)
    wb_status: dict[str, str] = Field(default_factory=dict)

    # ошибки/предупреждения
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @classmethod
    def from_in(cls, p: ProductIn) -> "ProductState":
        return cls(
            idx=p.idx,
            sku=p.sku,
            name=p.name,
            tg_file_id=p.tg_file_id,
            src_url=p.src_url,
            brand=p.brand,
        )


# ─── отчёт ──────────────────────────────────────────────────────────


class ReportItem(BaseModel):
    sku: str
    mp: str  # "ozon" | "wb"
    field: str | None = None
    reason: str | None = None
    marketplace_id: str | None = None


class Report(BaseModel):
    batch_id: str
    total: int
    successes: list[ReportItem] = Field(default_factory=list)
    errors: list[ReportItem] = Field(default_factory=list)
    warnings: list[ReportItem] = Field(default_factory=list)
