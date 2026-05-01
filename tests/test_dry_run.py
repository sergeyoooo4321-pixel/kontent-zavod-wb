"""Тест на DRY_RUN: upload_ozon / upload_wb должны НЕ дёргать API
и шлать payload в TG."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models import CategoryRef, ProductState
from app.pipeline import CategoryData, Deps, upload_ozon, upload_wb


def _state(sku: str = "TEST_001") -> ProductState:
    s = ProductState(idx=0, sku=sku, name="Тестовый товар", tg_file_id="A" * 40)
    s.ozon_category = CategoryRef(id=17027949, type_id=92364, path="Категории / Тест")
    s.wb_subject = CategoryRef(id=4459, path="Тест WB")
    s.images = {"main": f"https://s3/{sku}_main.jpg"}
    s.src_url = f"https://s3/{sku}_src.jpg"
    s.skus_3 = [
        {"sku": sku, "qty": 1, "weight_packed_g": 100, "weight_wb_kg": 0.1,
         "dims": {"l": 10, "w": 5, "h": 3}},
    ]
    s.titles[sku] = {"title_ozon": "X", "title_wb_short": "X", "title_wb_full": "X",
                     "annotation_ozon": "Y", "composition_wb": "Z"}
    s.attributes_ozon[sku] = []
    s.characteristics_wb[sku] = []
    return s


@pytest.fixture
def cat_data() -> dict:
    return {
        (17027949, 92364, 4459): CategoryData(
            ozon_attrs=[], ozon_attr_values={},
            wb_charcs=[], wb_charc_values={},
        )
    }


@pytest.mark.asyncio
async def test_upload_ozon_dry_run_skips_api_and_sends_to_tg(monkeypatch, cat_data):
    from app import config as cfg
    cfg.settings.OZON_CLIENT_ID = "x"
    cfg.settings.OZON_API_KEY = "y"
    cfg.settings.DRY_RUN = True

    tg = MagicMock()
    tg.send = AsyncMock()
    tg.send_document = AsyncMock()

    ozon = MagicMock()
    ozon.import_products = AsyncMock(side_effect=AssertionError("must not be called in DRY_RUN"))
    ozon.import_wait = AsyncMock(side_effect=AssertionError("must not be called in DRY_RUN"))

    s3 = MagicMock()
    wb = MagicMock()
    kie = MagicMock()
    deps = Deps(tg=tg, kie=kie, s3=s3, ozon=ozon, wb=wb)

    rep = await upload_ozon([_state()], cat_data, deps, chat_id=42)

    assert rep.total == 1
    # successes пустые — ничего реально не уехало
    assert rep.successes == []
    # warnings содержат [DRY_RUN] пометку
    assert any("[DRY_RUN]" in (w.reason or "") for w in rep.warnings)
    # TG получил summary + JSON-документ
    assert tg.send.await_count >= 1
    assert tg.send_document.await_count == 1
    # JSON-документ содержит наш SKU
    sent_doc_args = tg.send_document.call_args_list[0]
    payload_bytes = sent_doc_args.args[1] if len(sent_doc_args.args) > 1 else sent_doc_args.kwargs.get("data") or b""
    assert b"TEST_001" in payload_bytes
    # API не дёргался
    ozon.import_products.assert_not_awaited()


@pytest.mark.asyncio
async def test_upload_wb_dry_run_skips_api_and_sends_to_tg(monkeypatch, cat_data):
    from app import config as cfg
    cfg.settings.WB_TOKEN = "tok"
    cfg.settings.DRY_RUN = True

    tg = MagicMock()
    tg.send = AsyncMock()
    tg.send_document = AsyncMock()

    wb = MagicMock()
    wb.upload_cards = AsyncMock(side_effect=AssertionError("must not be called in DRY_RUN"))
    wb.upload_wait = AsyncMock(side_effect=AssertionError("must not be called in DRY_RUN"))

    deps = Deps(tg=tg, kie=MagicMock(), s3=MagicMock(), ozon=MagicMock(), wb=wb)
    rep = await upload_wb([_state()], cat_data, deps, chat_id=42)

    assert rep.total == 1
    assert any("[DRY_RUN]" in (w.reason or "") for w in rep.warnings)
    assert tg.send_document.await_count == 1
    wb.upload_cards.assert_not_awaited()
