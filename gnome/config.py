from __future__ import annotations
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


GNOME_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(GNOME_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # AI-провайдер: aitunnel.ru
    AITUNNEL_API_KEY: str = ""
    AITUNNEL_BASE: str = "https://api.aitunnel.ru/v1"
    LLM_MODEL: str = "gemini-3.1-pro-preview"
    LLM_FALLBACK_MODEL: str = "claude-sonnet-4.6"

    AGENT_PORT: int = 8001
    AGENT_HOST: str = "127.0.0.1"

    COMPACT_AT_TOKENS: int = 80_000
    MAX_STEPS: int = 10
    LOG_LEVEL: str = "INFO"

    # Для скиллов которые ходят в cz-backend internal API
    BACKEND_URL: str = "http://127.0.0.1:8000"
    INTERNAL_TOKEN: str = ""

    # Корень проекта — для file_read/grep/glob ограничения и read_logs.
    # По умолчанию — родительская папка от gnome/ (т.е. корень репо).
    WORKSPACE_ROOT: str = ""

    @property
    def workspace_root(self) -> Path:
        if self.WORKSPACE_ROOT:
            return Path(self.WORKSPACE_ROOT).resolve()
        return GNOME_DIR.parent.resolve()

    @property
    def data_dir(self) -> Path:
        d = GNOME_DIR / "data"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def archive_dir(self) -> Path:
        d = GNOME_DIR / "sessions" / "archive"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def memory_dir(self) -> Path:
        return GNOME_DIR / "memory"

    @property
    def skills_dir(self) -> Path:
        return GNOME_DIR / "skills"

    @property
    def claude_md(self) -> Path:
        return GNOME_DIR / "CLAUDE.md"


settings = Settings()
