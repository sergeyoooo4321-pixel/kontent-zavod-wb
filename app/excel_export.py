from __future__ import annotations

import csv
import io
import re
from zipfile import ZIP_DEFLATED, ZipFile

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from app.config import Settings
from app.models import ProductInput, ProductResult, SkuVariant


def build_sku_variants(product: ProductInput, settings: Settings) -> list[SkuVariant]:
    weight = product.weight_g or settings.DEFAULT_WEIGHT_G
    length = product.length_cm or settings.DEFAULT_LENGTH_CM
    width = product.width_cm or settings.DEFAULT_WIDTH_CM
    height = product.height_cm or settings.DEFAULT_HEIGHT_CM
    variants = []
    for qty in (1, 2, 3):
        sku = product.sku if qty == 1 else f"{product.sku}x{qty}"
        ozon_title = _ozon_title(product, qty)
        wb_short = _limit(_strip_brand(product.name, product.brand), 60)
        wb_full = _wb_full_title(product, qty)
        dims = _pack_dims(length, width, height, qty)
        variants.append(
            SkuVariant(
                sku=sku,
                qty=qty,
                ozon_title=ozon_title,
                wb_short_title=wb_short,
                wb_full_title=wb_full,
                weight_g=_round_weight(weight * qty),
                length_cm=dims[0],
                width_cm=dims[1],
                height_cm=dims[2],
            )
        )
    return variants


def build_zip(results: list[ProductResult], settings: Settings) -> bytes:
    links_csv = _links_csv(results)
    ozon = _workbook_bytes("ozon", results, settings)
    wb = _workbook_bytes("wb", results, settings)
    readme = _readme(results)
    out = io.BytesIO()
    with ZipFile(out, "w", ZIP_DEFLATED) as zf:
        zf.writestr("links.csv", links_csv)
        zf.writestr("ozon.xlsx", ozon)
        zf.writestr("wildberries.xlsx", wb)
        zf.writestr("README.txt", readme)
        for result in results:
            for image in result.images:
                if image.bytes_data and image.role != "source":
                    zf.writestr(f"photos/{result.input.sku}_{image.role}.jpg", image.bytes_data)
    return out.getvalue()


def _workbook_bytes(kind: str, results: list[ProductResult], settings: Settings) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Ozon" if kind == "ozon" else "Wildberries"
    headers = _ozon_headers() if kind == "ozon" else _wb_headers()
    ws.append(headers)
    for result in results:
        images = {img.role: img.url for img in result.images}
        for variant in build_sku_variants(result.input, settings):
            if kind == "ozon":
                ws.append(
                    [
                        variant.sku,
                        variant.ozon_title,
                        result.input.brand,
                        _description(result.input, variant.qty),
                        "0.22",
                        variant.weight_g,
                        variant.weight_g,
                        variant.length_cm * 10,
                        variant.width_cm * 10,
                        variant.height_cm * 10,
                        "mm",
                        result.input.price or settings.DEFAULT_PRICE,
                        images.get("main", ""),
                        images.get("pack2", ""),
                        images.get("pack3", ""),
                        images.get("extra", ""),
                        result.input.extra,
                    ]
                )
            else:
                ws.append(
                    [
                        variant.sku,
                        variant.wb_short_title,
                        variant.wb_full_title,
                        result.input.brand,
                        _description(result.input, variant.qty),
                        variant.length_cm,
                        variant.width_cm,
                        variant.height_cm,
                        round(variant.weight_g / 1000, 3),
                        result.input.price or settings.DEFAULT_PRICE,
                        images.get("main", ""),
                        images.get("pack2", ""),
                        images.get("pack3", ""),
                        images.get("extra", ""),
                        result.input.extra,
                    ]
                )
    _style(ws)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _ozon_headers() -> list[str]:
    return [
        "offer_id",
        "name",
        "brand",
        "description",
        "vat",
        "weight",
        "weight_packed_g",
        "depth_mm",
        "width_mm",
        "height_mm",
        "dimension_unit",
        "price",
        "primary_image",
        "image_pack2",
        "image_pack3",
        "image_extra",
        "comment",
    ]


def _wb_headers() -> list[str]:
    return [
        "vendorCode",
        "title_60",
        "full_title",
        "brand",
        "description",
        "length_cm",
        "width_cm",
        "height_cm",
        "weightBrutto_kg",
        "price",
        "media_main",
        "media_pack2",
        "media_pack3",
        "media_extra",
        "comment",
    ]


def _style(ws) -> None:
    fill = PatternFill("solid", fgColor="1F3A4A")
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = fill
    for column in ws.columns:
        letter = get_column_letter(column[0].column)
        width = min(max(len(str(cell.value or "")) for cell in column) + 2, 48)
        ws.column_dimensions[letter].width = width
    ws.freeze_panes = "A2"


def _links_csv(results: list[ProductResult]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["sku", "photo_index", "role", "url"])
    for result in results:
        for image in result.images:
            writer.writerow([result.input.sku, result.input.photo_index, image.role, image.url])
    return out.getvalue()


def _readme(results: list[ProductResult]) -> str:
    lines = [
        "Content Zavod export pack",
        "",
        "Files:",
        "- photos/: generated JPG files",
        "- links.csv: SKU-to-image public links",
        "- ozon.xlsx: Ozon workbook",
        "- wildberries.xlsx: Wildberries workbook",
        "",
        "Warnings:",
    ]
    warnings = [warning for result in results for warning in result.warnings]
    lines.extend(f"- {warning}" for warning in warnings)
    if not warnings:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def _description(product: ProductInput, qty: int) -> str:
    prefix = f"Набор {qty} шт. " if qty > 1 else ""
    brand = f"{product.brand} " if product.brand else ""
    facts = product.extra or "Подходит для ежедневного использования. Удобный формат для маркетплейса."
    return (
        f"{prefix}{brand}{product.name}. {facts} "
        "Товар подготовлен для карточки маркетплейса. Описание адаптировано под покупателя. "
        "Параметры и визуальные материалы проверяются оператором перед загрузкой."
    )


def _ozon_title(product: ProductInput, qty: int) -> str:
    base = " ".join(x for x in [product.brand, _strip_brand(product.name, product.brand)] if x).replace(" - ", " ")
    return base if qty == 1 else f"Набор {qty} шт {base}"


def _wb_full_title(product: ProductInput, qty: int) -> str:
    base = " ".join(x for x in [product.brand, _strip_brand(product.name, product.brand)] if x)
    return base if qty == 1 else f"Набор {qty} шт {base}"


def _strip_brand(name: str, brand: str) -> str:
    value = name.strip()
    if brand and value.lower().startswith(brand.lower()):
        value = value[len(brand) :].strip(" -")
    return value


def _limit(value: str, limit: int) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) <= limit:
        return value
    cut = value[: limit - 1].rsplit(" ", 1)[0]
    return f"{cut}…" if cut else value[: limit - 1] + "…"


def _pack_dims(length: int, width: int, height: int, qty: int) -> tuple[int, int, int]:
    dims = [length, width, height]
    if qty > 1:
        idx = min(range(3), key=lambda i: dims[i])
        dims[idx] *= qty
    return tuple(int(x) for x in dims)  # type: ignore[return-value]


def _round_weight(value: int) -> int:
    if value <= 100:
        return int(((value + 9) // 10) * 10)
    return int(((value + 49) // 50) * 50)

