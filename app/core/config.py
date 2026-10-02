import uuid
from functools import lru_cache
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class HardeningSettings(BaseSettings):
    """Settings the public routes and middleware need. They load without a database URL."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Rate limits (in process, per app instance; see app/core/rate_limit.py).
    rate_limit_enabled: bool = True
    rate_limit_public_per_minute: int = Field(default=120, gt=0)
    rate_limit_api_per_minute: int = Field(default=600, gt=0)
    rate_limit_ticket_create_per_minute: int = Field(default=10, gt=0)
    rate_limit_comment_per_minute: int = Field(default=30, gt=0)
    max_request_body_bytes: int = Field(default=65_536, gt=0)
    api_docs_enabled: bool = True


class Settings(HardeningSettings):
    """All configuration comes from environment variables (or a local .env file)."""

    database_url: SecretStr
    db_pool_size: int = Field(default=5, gt=0)
    db_max_overflow: int = Field(default=5, ge=0)
    db_pool_timeout_seconds: int = Field(default=10, gt=0)
    db_pool_recycle_seconds: int = Field(default=1800, gt=0)
    bootstrap_admin_sub: uuid.UUID | None = None
    # Token issuer: the local Supabase stack (`supabase start`). See docs/local-supabase.md.
    supabase_url: str = "http://127.0.0.1:54321"
    supabase_jwt_audience: str = "authenticated"
    jwks_timeout_seconds: float = Field(default=3.0, gt=0)
    jwks_cache_seconds: int = Field(default=300, gt=0)
    jwks_min_refetch_seconds: int = Field(default=30, gt=0)
    ai_timeout_seconds: float = Field(default=15.0, gt=0)
    # Gemini adapter: without a key, triage always takes the keyword fallback.
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-3.5-flash-lite"
    # Recovery sweeper for tickets stuck in ai_status = pending (docs/adr/0006).
    ai_recovery_enabled: bool = True
    ai_recovery_interval_seconds: int = Field(default=60, gt=0)
    ai_recovery_age_seconds: int = Field(default=120, gt=0)
    ai_recovery_batch_size: int = Field(default=10, gt=0)

    @field_validator("supabase_url")
    @classmethod
    def _require_https_unless_loopback(cls, value: str) -> str:
        """Signing keys fetched over plain http could be swapped by a man in the middle."""
        parsed = urlparse(value)
        if parsed.scheme == "https" or (
            parsed.scheme == "http" and parsed.hostname in LOOPBACK_HOSTS
        ):
            return value
        raise ValueError("must use https (plain http is only allowed for loopback hosts)")


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_hardening_settings() -> HardeningSettings:
    return HardeningSettings()
