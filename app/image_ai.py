from __future__ import annotations

import base64
import io
import textwrap
from dataclasses import dataclass

import httpx
from PIL import Image, ImageDraw, ImageFont, ImageOps

from app.config import Settings, mask_secret
from app.models import ProductInput


VARIANTS = {
    "main": "Главное фото: бренд, тип товара, 2-3 преимущества, плашка с объемом/весом.",
    "pack2": "Набор 2 штуки: тот же дизайн, понятная надпись Набор 2 штуки.",
    "pack3": "Набор 3 штуки: тот же дизайн, понятная надпись Набор 3 штуки.",
    "extra": "Дополнительная инфографика: преимущества, способ применения, состав или назначение.",
}


@dataclass
class ImageOutput:
    role: str
    content: bytes
    warning: str | None = None


class ImageGenerator:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def generate_set(self, product: ProductInput, source: bytes) -> list[ImageOutput]:
        outputs: list[ImageOutput] = []
        for role, instruction in VARIANTS.items():
            try:
                content = await self._generate_ai(product, source, instruction)
                outputs.append(ImageOutput(role=role, content=content))
            except Exception as exc:  # noqa: BLE001
                fallback = self._fallback_image(product, source, role)
                outputs.append(ImageOutput(role=role, content=fallback, warning=f"AI image fallback for {role}: {str(exc)[:160]}"))
        return outputs

    async def _generate_ai(self, product: ProductInput, source: bytes, instruction: str) -> bytes:
        if not self.settings.AITUNNEL_API_KEY:
            raise RuntimeError("AITUNNEL_API_KEY is not configured")
        prompt = self._prompt(product, instruction)
        url = f"{self.settings.AITUNNEL_BASE.rstrip('/')}/images/edits"
        headers = {"Authorization": f"Bearer {self.settings.AITUNNEL_API_KEY}"}
        files = {"image": ("source.jpg", source, "image/jpeg")}
        data = {"model": self.settings.AITUNNEL_IMAGE_MODEL, "prompt": prompt, "size": self.settings.IMAGE_SIZE, "n": "1"}
        async with httpx.AsyncClient(timeout=self.settings.HTTP_TIMEOUT_SEC) as client:
            response = await client.post(url, headers=headers, data=data, files=files)
        if response.status_code >= 400:
            text = response.text.replace(self.settings.AITUNNEL_API_KEY, mask_secret(self.settings.AITUNNEL_API_KEY))
            raise RuntimeError(f"image API HTTP {response.status_code}: {text[:400]}")
        payload = response.json()
        item = (payload.get("data") or [{}])[0]
        if item.get("b64_json"):
            return base64.b64decode(item["b64_json"])
        if item.get("url"):
            async with httpx.AsyncClient(timeout=self.settings.HTTP_TIMEOUT_SEC) as client:
                img = await client.get(item["url"])
            img.raise_for_status()
            return img.content
        raise RuntimeError("image API returned no image")

    def _prompt(self, product: ProductInput, instruction: str) -> str:
        return (
            "Создай маркетплейс-фото товара для Ozon/Wildberries. "
            "Сохрани узнаваемость упаковки с исходного фото, не искажай бренд и текст на товаре. "
            "Формат вертикальный, чистая коммерческая инфографика, без запрещенных обещаний. "
            f"Артикул: {product.sku}. Бренд: {product.brand or 'не указан'}. "
            f"Товар: {product.name}. Дополнительные данные: {product.extra or 'нет'}. "
            f"Задача кадра: {instruction}"
        )

    def _fallback_image(self, product: ProductInput, source: bytes, role: str) -> bytes:
        width, height = _parse_size(self.settings.IMAGE_SIZE)
        canvas = Image.new("RGB", (width, height), (245, 242, 235))
        draw = ImageDraw.Draw(canvas)
        source_img = Image.open(io.BytesIO(source)).convert("RGB")
        source_img = ImageOps.exif_transpose(source_img)
        source_img.thumbnail((int(width * 0.76), int(height * 0.58)))
        x = (width - source_img.width) // 2
        y = int(height * 0.22)
        canvas.paste(source_img, (x, y))

        font_big = _font(54)
        font_mid = _font(38)
        font_small = _font(28)
        draw.rectangle((0, 0, width, int(height * 0.18)), fill=(34, 54, 74))
        draw.text((48, 42), (product.brand or "Бренд").upper(), fill="white", font=font_big)
        draw.text((48, 118), _wrap_one(product.name, 30), fill=(230, 240, 245), font=font_mid)
        badge = {"main": "Главное фото", "pack2": "Набор 2 штуки", "pack3": "Набор 3 штуки", "extra": "Доп. инфографика"}[role]
        draw.rounded_rectangle((48, height - 210, width - 48, height - 72), radius=28, fill=(235, 111, 55))
        draw.text((76, height - 178), badge, fill="white", font=font_big)
        if product.extra:
            for i, line in enumerate(textwrap.wrap(product.extra, width=46)[:2]):
                draw.text((76, height - 108 + i * 32), line, fill="white", font=font_small)
        out = io.BytesIO()
        canvas.save(out, format="JPEG", quality=92, optimize=True)
        return out.getvalue()


def _parse_size(value: str) -> tuple[int, int]:
    try:
        w, h = value.lower().split("x", 1)
        return int(w), int(h)
    except Exception:
        return 1024, 1536


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in ("C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap_one(text: str, width: int) -> str:
    wrapped = textwrap.wrap(text, width=width)
    return wrapped[0] if wrapped else text

