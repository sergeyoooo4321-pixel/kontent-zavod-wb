"""Конфигурация: pydantic-settings, читает из env / .env файла.

Поддерживает несколько кабинетов Ozon + WB через именованные env-переменные.
Имена кабинетов: profit, progress24, progress247, tnp.
Внутри кода каждый кабинет — это пара Ozon+WB; если у кабинета только WB
(progress247) — поле .ozon будет None, заливка идёт только на WB.
"""
from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# ─── описание кабинетов ───────────────────────────────────────────

# Каноничный порядок и человеческие лейблы для UI бота
CABINET_ORDER: list[str] = ["profit", "progress24", "progress247", "tnp", "default"]
CABINET_LABELS: dict[str, str] = {
    "profit": "Профит",
    "progress24": "Прогресс 24",
    "progress247": "Прогресс 247",
    "tnp": "ТНП",
    "default": "Default",
}


class OzonCabinetConfig(BaseModel):
    client_id: str
    api_key: str


class WBCabinetConfig(BaseModel):
    token: str


class Cabinet(BaseModel):
    """Один кабинет = опционально Ozon + опционально WB.

    name — внутренний идентификатор (profit / progress24 / ...).
    label — человеческое имя для UI (Профит / Прогресс 24 / ...).
    """
    name: str
    label: str
    ozon: OzonCabinetConfig | None = None
    wb: WBCabinetConfig | None = None

    @property
    def has_ozon(self) -> bool:
        return self.ozon is not None

    @property
    def has_wb(self) -> bool:
        return self.wb is not None


# ─── Settings ─────────────────────────────────────────────────────


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
    # Лимит kie.ai: 20 createTask / 10 сек = 2 req/sec. Token-bucket throttle
    # в KieAIClient гарантирует не превышать; занижение = больше задержка между
    # createTask, но 0% шанс получить 429.
    KIE_RATE_PER_SEC: float = 2.0
    # Concurrent createTask. kie.ai допускает 100+, но 6 — безопасный default
    # чтобы не штормить при переборке партии.
    KIE_MAX_CONCURRENT: int = 6

    # Yandex Object Storage
    S3_ENDPOINT: str = "https://storage.yandexcloud.net"
    S3_REGION: str = "ru-central1"
    S3_BUCKET: str = "cz-content-zavod-prod"
    S3_ACCESS_KEY: str
    S3_SECRET_KEY: str
    S3_PUBLIC_BASE: str = "https://storage.yandexcloud.net/cz-content-zavod-prod"

    # ── Маркетплейсы: общие base URLs ─────────────────────
    OZON_BASE: str = "https://api-seller.ozon.ru"
    WB_BASE: str = "https://content-api.wildberries.ru"

    # ── Кабинеты Ozon (по 2 переменные на кабинет) ────────
    OZON_PROFIT_CLIENT_ID: str | None = None
    OZON_PROFIT_API_KEY: str | None = None
    OZON_PROGRESS24_CLIENT_ID: str | None = None
    OZON_PROGRESS24_API_KEY: str | None = None
    OZON_PROGRESS247_CLIENT_ID: str | None = None
    OZON_PROGRESS247_API_KEY: str | None = None
    OZON_TNP_CLIENT_ID: str | None = None
    OZON_TNP_API_KEY: str | None = None

    # ── Кабинеты WB (по 1 JWT-токену) ─────────────────────
    WB_PROFIT_TOKEN: str | None = None
    WB_PROGRESS24_TOKEN: str | None = None
    WB_PROGRESS247_TOKEN: str | None = None
    WB_TNP_TOKEN: str | None = None

    # ── Backward-compat: один безымянный кабинет "default" ──
    OZON_CLIENT_ID: str | None = None
    OZON_API_KEY: str | None = None
    WB_TOKEN: str | None = None

    # DRY_RUN: при True upload_ozon / upload_wb НЕ делают реальные POST в API
    # маркетплейсов — собирают payload, шлют его в TG как JSON-документ.
    # Можно переключать прямо из бота в runtime через кнопку «Настройки».
    DRY_RUN: bool = False

    # Runtime
    MAX_PARALLEL_PRODUCTS: int = 10
    HTTP_TIMEOUT_SEC: int = 60
    LLM_TEMPERATURE: float = 0.2
    LOG_LEVEL: str = "INFO"
    INTERNAL_TOKEN: str | None = None

    # ── Хелперы по кабинетам ──────────────────────────────

    def _build_ozon_for(self, name: str) -> OzonCabinetConfig | None:
        if name == "default":
            cid, key = self.OZON_CLIENT_ID, self.OZON_API_KEY
        else:
            cid = getattr(self, f"OZON_{name.upper()}_CLIENT_ID", None)
            key = getattr(self, f"OZON_{name.upper()}_API_KEY", None)
        if cid and key:
            return OzonCabinetConfig(client_id=cid, api_key=key)
        return None

    def _build_wb_for(self, name: str) -> WBCabinetConfig | None:
        if name == "default":
            tok = self.WB_TOKEN
        else:
            tok = getattr(self, f"WB_{name.upper()}_TOKEN", None)
        if tok:
            return WBCabinetConfig(token=tok)
        return None

    def list_cabinets(self) -> list[Cabinet]:
        """Все настроенные кабинеты в каноничном порядке.

        Кабинет считается настроенным если есть хотя бы одна из его сторон
        (Ozon ИЛИ WB). Кабинет с обеими сторонами выглядит «полным».
        """
        out: list[Cabinet] = []
        for name in CABINET_ORDER:
            ozon = self._build_ozon_for(name)
            wb = self._build_wb_for(name)
            if ozon is None and wb is None:
                continue
            out.append(Cabinet(
                name=name,
                label=CABINET_LABELS.get(name, name.title()),
                ozon=ozon,
                wb=wb,
            ))
        return out

    def get_cabinet(self, name: str) -> Cabinet | None:
        for c in self.list_cabinets():
            if c.name == name:
                return c
        return None

    @property
    def default_cabinet_name(self) -> str | None:
        cabs = self.list_cabinets()
        return cabs[0].name if cabs else None

    # ── Backward-compat properties (для существующих мест в коде) ──

    @property
    def has_ozon_creds(self) -> bool:
        """True если хотя бы один кабинет имеет Ozon credentials."""
        return any(c.has_ozon for c in self.list_cabinets())

    @property
    def has_wb_creds(self) -> bool:
        """True если хотя бы один кабинет имеет WB token."""
        return any(c.has_wb for c in self.list_cabinets())


settings = Settings()  # type: ignore[call-arg]
