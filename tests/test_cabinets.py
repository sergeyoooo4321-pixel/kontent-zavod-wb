"""Тесты multi-cabinet конфига и mirror-режима в pipeline."""
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.skip(reason="тестировали удалённый pipeline.run_batch / upload_* / _build_wb_card")


def _reload_settings(monkeypatch, env: dict[str, str]) -> "Settings":
    """Создаёт новый Settings instance под заданный env (без перезагрузки модуля,
    чтобы не ломать binding settings в других модулях типа pipeline)."""
    for k in [
        "OZON_PROFIT_CLIENT_ID", "OZON_PROFIT_API_KEY",
        "OZON_PROGRESS24_CLIENT_ID", "OZON_PROGRESS24_API_KEY",
        "OZON_PROGRESS247_CLIENT_ID", "OZON_PROGRESS247_API_KEY",
        "OZON_TNP_CLIENT_ID", "OZON_TNP_API_KEY",
        "WB_PROFIT_TOKEN", "WB_PROGRESS24_TOKEN", "WB_PROGRESS247_TOKEN", "WB_TNP_TOKEN",
        "OZON_CLIENT_ID", "OZON_API_KEY", "WB_TOKEN",
    ]:
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    from app.config import Settings
    return Settings()


def test_cabinet_full_pair(monkeypatch):
    """Кабинет с обоими ozon+wb — has_ozon и has_wb True."""
    s = _reload_settings(monkeypatch, {
        "OZON_PROFIT_CLIENT_ID": "111",
        "OZON_PROFIT_API_KEY": "key1",
        "WB_PROFIT_TOKEN": "tok1",
    })
    cabs = s.list_cabinets()
    assert len(cabs) == 1
    assert cabs[0].name == "profit"
    assert cabs[0].label == "Профит"
    assert cabs[0].has_ozon is True
    assert cabs[0].has_wb is True
    assert cabs[0].ozon.client_id == "111"
    assert cabs[0].wb.token == "tok1"


def test_cabinet_wb_only(monkeypatch):
    """progress247 = только WB, .ozon = None."""
    s = _reload_settings(monkeypatch, {
        "WB_PROGRESS247_TOKEN": "wbonly",
    })
    cabs = s.list_cabinets()
    assert len(cabs) == 1
    assert cabs[0].name == "progress247"
    assert cabs[0].has_ozon is False
    assert cabs[0].has_wb is True


def test_cabinet_order_canonical(monkeypatch):
    """Кабинеты возвращаются в каноничном порядке: profit, progress24, progress247, tnp."""
    s = _reload_settings(monkeypatch, {
        "OZON_TNP_CLIENT_ID": "1", "OZON_TNP_API_KEY": "1",
        "WB_PROFIT_TOKEN": "p",
        "OZON_PROGRESS247_CLIENT_ID": "2", "OZON_PROGRESS247_API_KEY": "2",
        "OZON_PROGRESS24_CLIENT_ID": "3", "OZON_PROGRESS24_API_KEY": "3",
    })
    names = [c.name for c in s.list_cabinets()]
    assert names == ["profit", "progress24", "progress247", "tnp"]


def test_cabinet_default_backward_compat(monkeypatch):
    """Старые OZON_CLIENT_ID/OZON_API_KEY/WB_TOKEN дают кабинет 'default'."""
    s = _reload_settings(monkeypatch, {
        "OZON_CLIENT_ID": "999",
        "OZON_API_KEY": "old_key",
        "WB_TOKEN": "old_tok",
    })
    cabs = s.list_cabinets()
    assert len(cabs) == 1
    assert cabs[0].name == "default"
    assert cabs[0].has_ozon and cabs[0].has_wb


def test_cabinet_default_name_first(monkeypatch):
    """default_cabinet_name возвращает первый по каноничному порядку."""
    s = _reload_settings(monkeypatch, {
        "OZON_TNP_CLIENT_ID": "1", "OZON_TNP_API_KEY": "1",
        "OZON_PROFIT_CLIENT_ID": "2", "OZON_PROFIT_API_KEY": "2",
    })
    assert s.default_cabinet_name == "profit"


def test_get_cabinet_by_name(monkeypatch):
    s = _reload_settings(monkeypatch, {
        "OZON_PROFIT_CLIENT_ID": "1", "OZON_PROFIT_API_KEY": "k",
    })
    assert s.get_cabinet("profit").label == "Профит"
    assert s.get_cabinet("nonexistent") is None


def test_no_cabinets_configured(monkeypatch):
    """Если ничего не задано — пустой список."""
    s = _reload_settings(monkeypatch, {})
    assert s.list_cabinets() == []
    assert s.default_cabinet_name is None
    assert s.has_ozon_creds is False
    assert s.has_wb_creds is False


# ─── mirror в pipeline ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_batch_mirror_iterates_cabinets(monkeypatch):
    """В mirror-режиме upload_ozon/upload_wb вызываются по разу на каждый кабинет."""
    from app import config as cfg
    from app.config import Cabinet, OzonCabinetConfig, WBCabinetConfig

    cab1 = Cabinet(name="profit", label="Профит",
                   ozon=OzonCabinetConfig(client_id="1", api_key="k1"),
                   wb=WBCabinetConfig(token="t1"))
    cab2 = Cabinet(name="tnp", label="ТНП",
                   ozon=OzonCabinetConfig(client_id="2", api_key="k2"),
                   wb=None)

    cls = type(cfg.settings)
    monkeypatch.setattr(cls, "list_cabinets", lambda self: [cab1, cab2])
    monkeypatch.setattr(cls, "get_cabinet",
                        lambda self, name: {"profit": cab1, "tnp": cab2}.get(name))
    monkeypatch.setattr(cls, "default_cabinet_name", property(lambda self: "profit"))
    cfg.settings.DRY_RUN = True  # известное pydantic-поле — присваивается напрямую

    # Перехватываем upload_ozon / upload_wb
    upload_calls = []

    async def fake_upload_ozon(states, cat_data, deps, chat_id=None, cabinet=None):
        from app.models import Report
        upload_calls.append(("ozon", cabinet.name if cabinet else None))
        return Report(batch_id="", total=1)

    async def fake_upload_wb(states, cat_data, deps, chat_id=None, cabinet=None):
        from app.models import Report
        upload_calls.append(("wb", cabinet.name if cabinet else None))
        return Report(batch_id="", total=1)

    from app import pipeline as pipe
    monkeypatch.setattr(pipe, "upload_ozon", fake_upload_ozon)
    monkeypatch.setattr(pipe, "upload_wb", fake_upload_wb)

    # Минимальные моки для остальных стадий
    async def noop(*a, **k):
        return None

    monkeypatch.setattr(pipe, "process_product_images", noop)
    monkeypatch.setattr(pipe, "match_category", noop)
    monkeypatch.setattr(pipe, "build_skus_and_texts", noop)

    tg = MagicMock()
    tg.send = AsyncMock()
    tg.send_document = AsyncMock()

    ozon_mock = MagicMock()
    ozon_mock.category_tree = AsyncMock(return_value=[])
    wb_mock = MagicMock()
    wb_mock.subjects_tree = AsyncMock(return_value=[])

    deps = pipe.Deps(
        tg=tg, kie=MagicMock(), s3=MagicMock(),
        ozon=ozon_mock, wb=wb_mock, http=None,
    )

    from app.models import ProductIn, RunRequest
    req = RunRequest(
        batch_id="b1", chat_id=42,
        products=[ProductIn(idx=0, sku="A", name="Тест", tg_file_id="A" * 40)],
        cabinet_names=["profit", "tnp"],
    )
    await pipe.run_batch(req, deps)

    # Должно быть 4 вызова — по 2 (ozon+wb) на каждый кабинет
    assert ("ozon", "profit") in upload_calls
    assert ("wb", "profit") in upload_calls
    assert ("ozon", "tnp") in upload_calls
    assert ("wb", "tnp") in upload_calls
