"""Конфигурация: pydantic-settings, читает из env / .env файла."""
from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # Telegram
    TG_BOT_TOKEN: str
    TG_API_BASE: str = "https://api.telegram.org"

    # kie.ai
    KIE_BASE: str = "https://api.kie.ai"
    KIE_API_KEY: str
    KIE_IMAGE_MODEL: str = "gpt-image-2-image-to-image"
    KIE_LLM_MODEL: str = "gpt-5-2"
    KIE_POLL_INTERVAL_SEC: float = 5.0
    KIE_POLL_MAX_ATTEMPTS: int = 60

    # Pipeline mode (refactor_plan.md §4):
    # legacy — старый: 4 параллельные edit-генерации от src.
    # hybrid — новый: rembg + AI-фон + PIL composite + Playwright plashki.
    PIPELINE_MODE: Literal["legacy", "hybrid"] = "hybrid"

    # Yandex Object Storage
    S3_ENDPOINT: str = "https://storage.yandexcloud.net"
    S3_REGION: str = "ru-central1"
    S3_BUCKET: str = "cz-content-zavod-prod"
    S3_ACCESS_KEY: str
    S3_SECRET_KEY: str
    S3_PUBLIC_BASE: str = "https://storage.yandexcloud.net/cz-content-zavod-prod"

    # Ozon
    OZON_BASE: str = "https://api-seller.ozon.ru"
    OZON_CLIENT_ID: str | None = None
    OZON_API_KEY: str | None = None

    # Wildberries
    WB_BASE: str = "https://content-api.wildberries.ru"
    WB_TOKEN: str | None = None

    # Runtime — параллельные товары (внутри товара: main → потом 3 параллельно).
    # Глобальный лимит kie.ai = 8 параллельных запросов (KieAIClient._sem),
    # так что фактический параллелизм ограничен этим.
    MAX_PARALLEL_PRODUCTS: int = 10
    HTTP_TIMEOUT_SEC: int = 60
    LLM_TEMPERATURE: float = 0.2
    LOG_LEVEL: str = "INFO"
    INTERNAL_TOKEN: str | None = None

    @property
    def has_ozon_creds(self) -> bool:
        return bool(self.OZON_CLIENT_ID and self.OZON_API_KEY)

    @property
    def has_wb_creds(self) -> bool:
        return bool(self.WB_TOKEN)


settings = Settings()  # type: ignore[call-arg]
