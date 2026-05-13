from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    TG_BOT_TOKEN: str = ""
    TG_API_BASE: str = "https://api.telegram.org"
    TG_WEBHOOK_SECRET_TOKEN: str = ""

    PUBLIC_BASE_URL: str = ""
    APP_HOST: str = "127.0.0.1"
    APP_PORT: int = 8000
    LOG_LEVEL: str = "INFO"

    AITUNNEL_BASE: str = "https://api.aitunnel.ru/v1"
    AITUNNEL_API_KEY: str = ""
    AITUNNEL_IMAGE_MODEL: str = "gpt-image-2"
    AITUNNEL_LLM_MODEL: str = "gemini-3.1-pro-preview"
    AITUNNEL_LLM_FALLBACK_MODEL: str = "claude-sonnet-4.6"
    IMAGE_SIZE: str = "1024x1536"
    LLM_TEMPERATURE: float = 0.2

    OZON_BASE: str = "https://api-seller.ozon.ru"
    OZON_CLIENT_ID: str = ""
    OZON_API_KEY: str = ""
    OZON_PROFIT_CLIENT_ID: str = ""
    OZON_PROFIT_API_KEY: str = ""
    OZON_PROGRESS24_CLIENT_ID: str = ""
    OZON_PROGRESS24_API_KEY: str = ""
    OZON_TNP_CLIENT_ID: str = ""
    OZON_TNP_API_KEY: str = ""

    WB_BASE: str = "https://content-api.wildberries.ru"
    WB_TOKEN: str = ""
    WB_PROFIT_TOKEN: str = ""
    WB_PROGRESS24_TOKEN: str = ""
    WB_PROGRESS247_TOKEN: str = ""
    WB_TNP_TOKEN: str = ""

    MARKETPLACE_LIVE_ENABLED: bool = True
    MARKETPLACE_CACHE_TTL_SEC: int = 21600
    TEMPLATE_CACHE_DIR: Path = Path("runtime/templates")

    S3_ENDPOINT: str = "https://storage.yandexcloud.net"
    S3_REGION: str = "ru-central1"
    S3_BUCKET: str = ""
    S3_ACCESS_KEY: str = ""
    S3_SECRET_KEY: str = ""
    S3_PUBLIC_BASE: str = ""

    MEDIA_FALLBACK_DIR: Path = Path("media")
    MEDIA_PUBLIC_BASE: str = ""
    RUNTIME_DIR: Path = Path("runtime")
    SQLITE_PATH: Path = Path("runtime/content_zavod.sqlite3")

    MAX_PHOTOS_PER_BATCH: int = Field(default=50, ge=1, le=200)
    MAX_PARALLEL_PRODUCTS: int = Field(default=3, ge=1, le=10)
    HTTP_TIMEOUT_SEC: int = Field(default=90, ge=10, le=300)

    DEFAULT_PRICE: float = 0
    DEFAULT_WEIGHT_G: int = 100
    DEFAULT_LENGTH_CM: int = 10
    DEFAULT_WIDTH_CM: int = 10
    DEFAULT_HEIGHT_CM: int = 10

    @property
    def s3_enabled(self) -> bool:
        return bool(self.S3_BUCKET and self.S3_ACCESS_KEY and self.S3_SECRET_KEY)

    @property
    def ozon_credentials(self) -> tuple[str, str] | None:
        pairs = [
            (self.OZON_CLIENT_ID, self.OZON_API_KEY),
            (self.OZON_PROFIT_CLIENT_ID, self.OZON_PROFIT_API_KEY),
            (self.OZON_PROGRESS24_CLIENT_ID, self.OZON_PROGRESS24_API_KEY),
            (self.OZON_TNP_CLIENT_ID, self.OZON_TNP_API_KEY),
        ]
        return next(((client_id, api_key) for client_id, api_key in pairs if client_id and api_key), None)

    @property
    def wb_token(self) -> str:
        return next(
            (
                token
                for token in (
                    self.WB_TOKEN,
                    self.WB_PROFIT_TOKEN,
                    self.WB_PROGRESS24_TOKEN,
                    self.WB_PROGRESS247_TOKEN,
                    self.WB_TNP_TOKEN,
                )
                if token
            ),
            "",
        )


settings = Settings()


def mask_secret(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"
