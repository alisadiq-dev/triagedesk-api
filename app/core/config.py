import uuid
from functools import lru_cache
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
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
    api_docs_enabled: bool = False  # on for local development (make run), off everywhere else


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
    # Where to fetch the signing keys when that differs from the issuer address (the API runs in a
    # container and the Supabase stack on the host). The `iss` claim is still checked against
    # SUPABASE_URL. Plain http to a non-loopback host needs the explicit allow flag (local only).
    supabase_jwks_base_url: str | None = None
    supabase_jwks_allow_plain_http: bool = False
    jwks_timeout_seconds: float = Field(default=3.0, gt=0)
    jwks_cache_seconds: int = Field(default=300, gt=0)
    jwks_min_refetch_seconds: int = Field(default=30, gt=0)
    ai_timeout_seconds: float = Field(default=15.0, gt=0)
    # Global cap on model calls per minute; over it, tickets take the keyword fallback.
    ai_max_output_tokens: int = Field(default=1024, gt=0)  # a cut-off answer takes the fallback
    ai_calls_per_minute: int = Field(default=60, gt=0)
    # Gemini adapter: without a key, triage always takes the keyword fallback.
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-3.5-flash-lite"
    # Recovery sweeper for tickets stuck in ai_status = pending (docs/adr/0006).
    ai_recovery_enabled: bool = True
    ai_recovery_interval_seconds: int = Field(default=60, gt=0)
    ai_recovery_age_seconds: int = Field(default=120, gt=0)
    ai_recovery_batch_size: int = Field(default=10, gt=0)
    ai_recovery_max_attempts: int = Field(default=3, gt=0)  # then a ticket is left alone

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

    @field_validator("supabase_jwks_base_url", mode="before")
    @classmethod
    def _empty_means_unset(cls, value: object) -> object:
        return None if value == "" else value

    @model_validator(mode="after")
    def _check_jwks_base_url(self) -> "Settings":
        url = self.supabase_jwks_base_url
        if url is None:
            return self
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("supabase_jwks_base_url must be an http or https URL")
        plain_remote = parsed.scheme == "http" and parsed.hostname not in LOOPBACK_HOSTS
        if plain_remote and not self.supabase_jwks_allow_plain_http:
            raise ValueError(
                "supabase_jwks_base_url must use https (plain http to another host needs "
                "SUPABASE_JWKS_ALLOW_PLAIN_HTTP=true, for the local stack only)"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_hardening_settings() -> HardeningSettings:
    return HardeningSettings()
