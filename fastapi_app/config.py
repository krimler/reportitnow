"""Process-wide configuration loaded from env."""
from __future__ import annotations

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM
    llm_endpoint: str = "http://localhost:11434/v1"
    llm_model: str = "qwen3:30b-a3b"
    llm_api_key: str = "ollama"
    llm_request_timeout_s: int = 120
    llm_stub_mode: int = 0

    # DB. We accept REPORTITNOW_DATABASE_URL (preferred) or the legacy
    # DATABASE_URL. We deliberately do NOT set DATABASE_URL in the running
    # process: Chainlit 2.x reads that env var to spin up its own Postgres
    # data layer (and unconditionally imports asyncpg).
    database_url: str = Field(
        default="sqlite:///./data/reportitnow.db",
        validation_alias=AliasChoices(
            "REPORTITNOW_DATABASE_URL", "REPORTITNOW_DB_URL",
        ),
    )

    # Secrets
    audit_chain_hmac_key: str = "dev-only-change-me-please-32-bytes-min-xxxxx"
    service_token_secret: str = "dev-only-service-token-secret-change-me"

    # FastAPI
    fastapi_host: str = "127.0.0.1"
    fastapi_port: int = 8000
    fastapi_base_url: str = "http://127.0.0.1:8000"

    # DP engine
    dp_epsilon_count: float = 0.5
    dp_epsilon_rate: float = 0.5
    dp_epsilon_time: float = 0.5
    dp_max_resolution_days: int = 150
    dp_workforce_floor: int = 50


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
