"""Тесты гибридного pipeline (refactor_plan.md §4) и retry-обёрток (§5)."""
from __future__ import annotations

import io
import json

import httpx
import pytest
from PIL import Image

from app.kie_ai import KieAIClient, KieAIError


# ─── §5.1 — create_image_task_with_retry ──────────────────────────


@pytest.mark.asyncio
async def test_create_image_task_with_retry_succeeds_on_third_attempt(respx_mock):
    """Первые 2 попытки kie возвращает ошибку, на 3-й — taskId."""
    route = respx_mock.post("https://api.kie.ai/api/v1/jobs/createTask").mock(
        side_effect=[
            httpx.Response(500, json={"code": 500, "message": "internal"}),
            httpx.Response(500, json={"code": 500, "message": "internal"}),
            httpx.Response(200, json={"code": 200, "data": {"taskId": "tsk-3"}}),
        ]
    )
    async with httpx.AsyncClient() as http:
        client = KieAIClient(
            base_url="https://api.kie.ai",
            api_key="k",
            http=http,
            poll_interval=0.001,
            poll_max_attempts=1,
        )
        tid = await client.create_image_task_with_retry(
            prompt="hi",
            input_urls=["https://x/img.jpg"],
            max_retries=2,
        )
        assert tid == "tsk-3"
        assert route.call_count == 3


@pytest.mark.asyncio
async def test_create_image_task_with_retry_gives_up(respx_mock):
    """3 фейла подряд → KieAIError с понятным message."""
    respx_mock.post("https://api.kie.ai/api/v1/jobs/createTask").mock(
        return_value=httpx.Response(500, json={"code": 500, "message": "internal"})
    )
    async with httpx.AsyncClient() as http:
        client = KieAIClient(
            base_url="https://api.kie.ai",
            api_key="k",
            http=http,
            poll_interval=0.001,
            poll_max_attempts=1,
        )
        with pytest.raises(KieAIError) as exc:
            await client.create_image_task_with_retry(
                prompt="hi",
                input_urls=["https://x/img.jpg"],
                max_retries=2,
            )
        assert "after 3 attempts" in str(exc.value)


@pytest.mark.asyncio
async def test_create_image_task_with_retry_passes_extra_params(respx_mock):
    """image_weight, guidance_scale, seed уходят в payload."""
    route = respx_mock.post("https://api.kie.ai/api/v1/jobs/createTask").mock(
        return_value=httpx.Response(200, json={"code": 200, "data": {"taskId": "tsk-x"}})
    )
    async with httpx.AsyncClient() as http:
        client = KieAIClient(
            base_url="https://api.kie.ai",
            api_key="k",
            http=http,
            poll_interval=0.001,
            poll_max_attempts=1,
        )
        await client.create_image_task_with_retry(
            prompt="hi",
            input_urls=["https://x/img.jpg"],
            image_weight=0.85,
            guidance_scale=7.5,
            seed=42,
        )
        sent_body = json.loads(route.calls[0].request.content)
        assert sent_body["input"]["image_weight"] == 0.85
        assert sent_body["input"]["guidance_scale"] == 7.5
        assert sent_body["input"]["seed"] == 42


# ─── §4.3 — composite_card ────────────────────────────────────────


def _make_png(size: tuple[int, int], color: tuple[int, int, int, int] = (255, 0, 0, 255)) -> bytes:
    img = Image.new("RGBA", size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_jpeg(size: tuple[int, int], color: tuple[int, int, int] = (200, 200, 200)) -> bytes:
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


@pytest.mark.parametrize("units", [1, 2, 3])
def test_composite_card(units: int):
    """composite_card собирает 1/2/3 товара на фоне без падений."""
    from app.composite import CARD_H, CARD_W, composite_card

    bg = _make_jpeg((1200, 1600))
    product = _make_png((600, 800))

    out = composite_card(bg, product, units=units)
    assert isinstance(out, bytes)
    assert len(out) > 1000
    img = Image.open(io.BytesIO(out))
    assert img.size == (CARD_W, CARD_H)
    assert img.format == "JPEG"


def test_composite_card_invalid_units():
    """units не 1/2/3 → ValueError."""
    from app.composite import composite_card

    bg = _make_jpeg((100, 100))
    product = _make_png((50, 50))
    with pytest.raises(ValueError):
        composite_card(bg, product, units=4)  # type: ignore[arg-type]


# ─── §4.4 — render_html_to_png (мок Playwright) ───────────────────


@pytest.mark.asyncio
async def test_render_plashki_calls_playwright(monkeypatch):
    """render_html_to_png рендерит Jinja2-шаблон в HTML и зовёт chromium screenshot."""
    from unittest.mock import AsyncMock, MagicMock

    captured_html: dict[str, str] = {}

    fake_screenshot = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

    async def fake_set_content(html, **kw):
        captured_html["html"] = html

    page = MagicMock()
    page.set_content = AsyncMock(side_effect=fake_set_content)
    page.screenshot = AsyncMock(return_value=fake_screenshot)

    ctx = MagicMock()
    ctx.new_page = AsyncMock(return_value=page)

    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=ctx)
    browser.close = AsyncMock()

    chromium = MagicMock()
    chromium.launch = AsyncMock(return_value=browser)

    pw_obj = MagicMock()
    pw_obj.chromium = chromium

    class FakeAsyncPlaywright:
        async def __aenter__(self):
            return pw_obj

        async def __aexit__(self, *a):
            return False

    fake_async_playwright_module = MagicMock()
    fake_async_playwright_module.async_playwright = lambda: FakeAsyncPlaywright()

    import sys
    monkeypatch.setitem(sys.modules, "playwright.async_api", fake_async_playwright_module)

    from app.plashki import render_html_to_png

    out = await render_html_to_png(
        "card_plashki.html.j2",
        {
            "brand_text": "САНОКС",
            "category_text": "Чистый сток",
            "brand_color": "#FF0000",
            "category_color": "#00AEEF",
            "benefits": ["Быстро", "Безопасно", "Свежесть"],
            "volume_text": "750 мл",
            "units_caption": "",
        },
    )
    assert out == fake_screenshot
    html = captured_html["html"]
    assert "САНОКС" in html
    assert "Быстро" in html
    assert "750 мл" in html
    assert "#FF0000" in html


@pytest.mark.asyncio
async def test_overlay_plashki():
    """overlay_plashki накладывает PNG поверх JPEG-карточки и возвращает JPEG."""
    from app.plashki import overlay_plashki

    card = _make_jpeg((400, 600))
    plashki = _make_png((400, 600), color=(0, 255, 0, 100))

    out = await overlay_plashki(card, plashki)
    img = Image.open(io.BytesIO(out))
    assert img.format == "JPEG"
    assert img.size == (400, 600)


# ─── §4.2 — bg_remove (импорт-only smoke; rembg тяжёлый) ──────────


def test_bg_remove_module_imports():
    """app.bg_remove импортируется без скачивания U2Net (lazy session)."""
    from app import bg_remove
    assert hasattr(bg_remove, "remove_bg")
    # _session должен быть None пока не вызвали remove_bg
    assert bg_remove._session is None


# ─── §4.6 — _build_plashki_context ─────────────────────────────────


def test_plashki_context_main():
    """Для main: benefits есть, volume_text есть, units_caption пустой."""
    from app.pipeline import _build_plashki_context

    brief = {
        "design": {
            "brand_block": {"brand_text": "САНОКС", "category_text": "Чистый сток"},
            "benefits": ["Быстро", "Безопасно"],
            "volume_badge": {"text": "750 мл"},
            "palette": ["#FF0000", "#00AEEF"],
        }
    }
    ctx = _build_plashki_context(brief, "main")
    assert ctx["brand_text"] == "САНОКС"
    assert ctx["benefits"] == ["Быстро", "Безопасно"]
    assert ctx["volume_text"] == "750 мл"
    assert ctx["units_caption"] == ""
    assert ctx["brand_color"] == "#FF0000"


def test_plashki_context_pack2():
    """Для pack2: benefits есть, volume_text пустой (только на main), units_caption Набор 2."""
    from app.pipeline import _build_plashki_context

    brief = {
        "design": {
            "brand_block": {"brand_text": "X"},
            "benefits": ["a", "b"],
            "volume_badge": {"text": "1 л"},
        }
    }
    ctx = _build_plashki_context(brief, "pack2")
    assert ctx["units_caption"] == "Набор 2 штуки"
    assert ctx["volume_text"] == ""  # volume только на main
    assert ctx["benefits"] == ["a", "b"]


def test_plashki_context_extra():
    """Для extra: benefits пустые (это карточка способа применения)."""
    from app.pipeline import _build_plashki_context

    brief = {"design": {"brand_block": {"brand_text": "X"}, "benefits": ["a"]}}
    ctx = _build_plashki_context(brief, "extra")
    assert ctx["benefits"] == []
    assert ctx["units_caption"] == ""
