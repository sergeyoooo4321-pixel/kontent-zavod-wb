"""Тесты pipeline на моках всех клиентов."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models import RunRequest, ProductIn
from app.pipeline import Deps, run_batch
from app.s3 import S3Client


@pytest.mark.asyncio
async def test_run_batch_no_creds(monkeypatch):
    """Без OZON/WB ключей пайплайн должен пройти этап 1 (фото) и завершиться."""
    monkeypatch.delenv("OZON_CLIENT_ID", raising=False)
    monkeypatch.delenv("OZON_API_KEY", raising=False)
    monkeypatch.delenv("WB_TOKEN", raising=False)

    from app import config as cfg
    cfg.settings.OZON_CLIENT_ID = None
    cfg.settings.OZON_API_KEY = None
    cfg.settings.WB_TOKEN = None
    cfg.settings.MAX_PARALLEL_PRODUCTS = 1
    cfg.settings.KIE_POLL_INTERVAL_SEC = 0.001
    cfg.settings.KIE_POLL_MAX_ATTEMPTS = 1

    tg = MagicMock()
    tg.send = AsyncMock(return_value={"ok": True})
    tg.get_file_bytes = AsyncMock(return_value=b"\xff\xd8\xff" + b"\x00" * 100)

    s3 = MagicMock()
    s3.put_public = AsyncMock(side_effect=lambda k, d, ct=None: f"https://s3/{k}")
    s3.fetch = AsyncMock(return_value=b"\xff\xd8\xff" + b"\x00" * 100)
    s3.build_key = S3Client.build_key
    s3.start = AsyncMock()

    kie = MagicMock()
    kie.generate_image = AsyncMock(return_value="https://kie/x.png")
    kie.generate_image_with_retry = AsyncMock(return_value="https://kie/x.png")
    kie.fetch_or_decode_image = AsyncMock(return_value=b"fake-image-bytes")

    ozon = MagicMock()
    wb = MagicMock()

    deps = Deps(tg=tg, kie=kie, s3=s3, ozon=ozon, wb=wb)
    req = RunRequest(
        batch_id="b1",
        chat_id=42,
        products=[ProductIn(idx=0, sku="A", name="Кофе", tg_file_id="AgACAgIAA-test-file-id-001")],
    )
    await run_batch(req, deps)

    # tg.send должен был вызваться несколько раз (старт, прогресс по фото, статус по этапу)
    assert tg.send.call_count >= 2
    # s3.put_public — для src и main и до 3 паков
    assert s3.put_public.call_count >= 2
