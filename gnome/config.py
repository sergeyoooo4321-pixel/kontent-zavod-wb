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

    KIE_API_KEY: str = ""
    KIE_BASE: str = "https://api.kie.ai"
    LLM_MODEL: str = "gemini-3-pro"
    LLM_FALLBACK_MODEL: str = "gpt-5-2"

    AGENT_PORT: int = 8001
    AGENT_HOST: str = "127.0.0.1"

    COMPACT_AT_TOKENS: int = 80_000
    MAX_STEPS: int = 10
    LOG_LEVEL: str = "INFO"

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
