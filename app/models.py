"""Pydantic-модели для входящих запросов и внутреннего состояния пайплайна."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ─── входящие из n8n ──────────────────────────────────────────────


class ProductIn(BaseModel):
    idx: int
    sku: str
    name: str
    tg_file_id: str
    brand: str | None = None


class RunRequest(BaseModel):
    batch_id: str
    chat_id: int
    products: list[ProductIn] = Field(min_length=1, max_length=10)


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
