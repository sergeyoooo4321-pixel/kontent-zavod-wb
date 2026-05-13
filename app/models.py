from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field


Phase = Literal["collecting_photos", "collecting_items", "processing", "done"]


class PhotoIn(BaseModel):
    index: int = Field(ge=1)
    file_id: str
    kind: str = "photo"
    file_name: str | None = None
    mime_type: str | None = None


class ProductInput(BaseModel):
    photo_index: int = Field(ge=1)
    sku: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=300)
    brand: str = Field(default="", max_length=120)
    extra: str = Field(default="", max_length=2000)
    price: float | None = None
    weight_g: int | None = None
    length_cm: int | None = None
    width_cm: int | None = None
    height_cm: int | None = None


class BotState(BaseModel):
    phase: Phase = "collecting_photos"
    batch_id: str
    photos: list[PhotoIn] = Field(default_factory=list)
    products: list[ProductInput] = Field(default_factory=list)
    current_item_index: int = 1


class GeneratedImage(BaseModel):
    role: Literal["source", "main", "pack2", "pack3", "extra"]
    url: str
    key: str
    content_type: str = "image/jpeg"
    bytes_data: bytes | None = Field(default=None, exclude=True)


class CategoryMatch(BaseModel):
    marketplace: Literal["ozon", "wb"]
    id: int
    type_id: int | None = None
    path: str = ""
    score: float = 0.0


class MarketplaceFieldValue(BaseModel):
    id: str
    name: str
    required: bool = False
    value: str | int | float | list[str] | None = None
    allowed_values: list[str] = Field(default_factory=list)
    source: str = ""
    warning: str = ""


class MarketplaceProfile(BaseModel):
    ozon_category: CategoryMatch | None = None
    wb_subject: CategoryMatch | None = None
    ozon_fields: list[MarketplaceFieldValue] = Field(default_factory=list)
    wb_fields: list[MarketplaceFieldValue] = Field(default_factory=list)
    missing_required: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ProductResult(BaseModel):
    input: ProductInput
    images: list[GeneratedImage]
    marketplace: MarketplaceProfile | None = None
    warnings: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class SkuVariant:
    sku: str
    qty: int
    ozon_title: str
    wb_short_title: str
    wb_full_title: str
    weight_g: int
    length_cm: int
    width_cm: int
    height_cm: int


@dataclass
class BatchArtifacts:
    batch_id: str
    results: list[ProductResult]
    zip_bytes: bytes
    links_csv: str
    extra: dict[str, Any] = field(default_factory=dict)
