"""Environment-driven application settings (spec overview §13).

Field names map to the `.env.example` keys: ``APP_URL``, ``DATABASE_URL``,
``REDIS_URL``, ``ENCRYPTION_KEY``, ``GITHUB_APP_ID``, ``GITHUB_APP_PRIVATE_KEY``,
``GITHUB_WEBHOOK_SECRET``, ``GITHUB_CLIENT_ID``, ``GITHUB_CLIENT_SECRET``,
``LITELLM_BASE_URL`` and ``LITELLM_MASTER_KEY``.

Importing this module must never raise when the environment is empty: every
field has a sensible default, so the module is import-safe without secrets.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["Settings", "get_settings"]


class Settings(BaseSettings):
    """Runtime configuration loaded from the environment and ``.env``."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Server
    app_url: str = Field(default="http://localhost:8400", alias="APP_URL")
    database_url: str = Field(
        default="postgresql+asyncpg://slopolis:slopolis@localhost:5432/slopolis",
        alias="DATABASE_URL",
    )
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

    # Secrets
    encryption_key: str | None = Field(default=None, alias="ENCRYPTION_KEY")

    # GitHub App
    github_app_id: str | None = Field(default=None, alias="GITHUB_APP_ID")
    github_app_private_key: str | None = Field(default=None, alias="GITHUB_APP_PRIVATE_KEY")
    github_webhook_secret: str | None = Field(default=None, alias="GITHUB_WEBHOOK_SECRET")
    github_client_id: str | None = Field(default=None, alias="GITHUB_CLIENT_ID")
    github_client_secret: str | None = Field(default=None, alias="GITHUB_CLIENT_SECRET")

    # LiteLLM gateway
    litellm_base_url: str = Field(default="http://localhost:4000", alias="LITELLM_BASE_URL")
    litellm_master_key: str | None = Field(default=None, alias="LITELLM_MASTER_KEY")


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    Cached so every call site shares one parsed instance; tests can clear it
    with ``get_settings.cache_clear()``.
    """
    return Settings()
