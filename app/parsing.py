from __future__ import annotations

import json
import re

from app.models import ProductInput


KEY_MAP = {
    "артикул": "sku",
    "sku": "sku",
    "vendorcode": "sku",
    "vendor_code": "sku",
    "название": "name",
    "наименование": "name",
    "name": "name",
    "товар": "name",
    "бренд": "brand",
    "brand": "brand",
    "доп": "extra",
    "комментарий": "extra",
    "описание": "extra",
    "extra": "extra",
    "цена": "price",
    "price": "price",
    "вес": "weight_g",
    "weight": "weight_g",
    "габариты": "dims",
    "dims": "dims",
}


def parse_product_text(photo_index: int, text: str) -> ProductInput:
    text = text.strip()
    if not text:
        raise ValueError("empty product text")

    data: dict[str, object] = {}
    if text.startswith("{"):
        raw = json.loads(text)
        if isinstance(raw, dict):
            data.update(raw)
    else:
        for line in re.split(r"[\n;]+", text):
            if ":" not in line and "=" not in line:
                continue
            key, value = re.split(r"[:=]", line, maxsplit=1)
            norm = re.sub(r"[\s_-]+", "_", key.strip().lower())
            field = KEY_MAP.get(norm) or KEY_MAP.get(norm.replace("_", ""))
            if field:
                data[field] = value.strip()

    if not data:
        parts = [p.strip() for p in re.split(r"\s+\|\s+|\t", text) if p.strip()]
        if len(parts) >= 3:
            data["sku"], data["brand"], data["name"] = parts[0], parts[1], " ".join(parts[2:])
        else:
            m = re.match(r"^(\S+)\s+(.+)$", text, flags=re.S)
            if not m:
                raise ValueError("format is not recognized")
            data["sku"] = m.group(1).strip()
            data["name"] = m.group(2).strip()

    name = str(data.get("name") or "").strip()
    brand = str(data.get("brand") or "").strip()
    if not brand and " - " in name:
        brand, name = [x.strip() for x in name.split(" - ", 1)]

    dims = str(data.pop("dims", "") or "")
    dim_values = [int(x) for x in re.findall(r"\d+", dims)[:3]]
    for key, value in zip(("length_cm", "width_cm", "height_cm"), dim_values, strict=False):
        data[key] = value

    if "weight_g" in data and isinstance(data["weight_g"], str):
        m = re.search(r"\d+", data["weight_g"])
        data["weight_g"] = int(m.group(0)) if m else None
    if "price" in data and isinstance(data["price"], str):
        data["price"] = float(data["price"].replace(",", "."))

    payload = {
        "photo_index": photo_index,
        "sku": str(data.get("sku") or "").strip(),
        "name": name,
        "brand": brand,
        "extra": str(data.get("extra") or "").strip(),
        "price": data.get("price"),
        "weight_g": data.get("weight_g"),
        "length_cm": data.get("length_cm"),
        "width_cm": data.get("width_cm"),
        "height_cm": data.get("height_cm"),
    }
    if not payload["sku"] or not payload["name"]:
        raise ValueError("sku and name are required")
    return ProductInput.model_validate(payload)

