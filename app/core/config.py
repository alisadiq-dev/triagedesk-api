import uuid
from functools import lru_cache
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class Settings(BaseSettings):
    """All configuration comes from environment variables (or a local .env file)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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
