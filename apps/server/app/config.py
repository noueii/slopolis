"""Application configuration for the API server.

Wraps :func:`slopolis_core.settings.get_settings` (the shared environment
contract) and adds server-only settings: the cookie-signing secret and the
auth session lifetime. Import-safe with an empty environment — every field has
a development default.

Also exposes :data:`CAMEL` and :func:`to_camel`, the alias generator the wire
schemas use so every response serializes camelCase exactly like
``apps/web/src/api/contract.ts``.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic.alias_generators import to_camel
from pydantic_settings import BaseSettings, SettingsConfigDict

from slopolis_core.settings import Settings as CoreSettings
from slopolis_core.settings import get_settings as get_core_settings

__all__ = ["CAMEL", "AppSettings", "get_app_settings"]

#: The single alias generator shared by every response/request schema.
CAMEL = to_camel


class AppSettings(BaseSettings):
    """Server-specific configuration layered on the core settings."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    cookie_secret: str = Field(
        default="insecure-development-cookie-secret",
        alias="COOKIE_SECRET",
    )
    """Key used to sign the auth cookie. Must be set in production."""

    session_lifetime_seconds: int = Field(
        default=60 * 60 * 24 * 14,
        alias="SESSION_LIFETIME_SECONDS",
    )
    """How long an authenticated session stays valid (default: 14 days)."""

    cookie_name: str = Field(default="slopolis_session", alias="COOKIE_NAME")
    """Name of the signed httpOnly auth cookie."""

    cors_origins: str = Field(
        default="http://localhost:5173",
        alias="CORS_ORIGINS",
    )
    """Comma-separated dev origins allowed to call the API with credentials."""

    @property
    def cors_origin_list(self) -> list[str]:
        """Return the parsed, non-empty CORS origins."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def core(self) -> CoreSettings:
        """Return the shared core settings instance."""
        return get_core_settings()


@lru_cache
def get_app_settings() -> AppSettings:
    """Return the process-wide app settings singleton.

    Cached so every call site shares one parsed instance; tests can clear it
    with ``get_app_settings.cache_clear()``.
    """
    return AppSettings()
