import pytest
from pydantic import ValidationError

from app.core.config import Settings

DB_URL = "postgresql+asyncpg://user:s3cret-pw@localhost:5432/triagedesk"


def test_missing_database_url_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ValidationError, match="database_url"):
        Settings(_env_file=None)


def test_values_are_loaded_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    monkeypatch.setenv("DB_POOL_SIZE", "7")

    settings = Settings(_env_file=None)

    assert settings.database_url.get_secret_value() == DB_URL
    assert settings.db_pool_size == 7


def test_database_url_is_masked_in_repr_and_str(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", DB_URL)

    settings = Settings(_env_file=None)

    assert "s3cret-pw" not in repr(settings)
    assert "s3cret-pw" not in str(settings)


def test_pool_defaults_are_explicit_and_sane(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    for name in ("DB_POOL_SIZE", "DB_MAX_OVERFLOW", "DB_POOL_TIMEOUT_SECONDS"):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=None)

    assert settings.db_pool_size == 5
    assert settings.db_max_overflow == 5
    assert settings.db_pool_timeout_seconds == 10


def test_pool_size_must_be_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    monkeypatch.setenv("DB_POOL_SIZE", "0")

    with pytest.raises(ValidationError, match="db_pool_size"):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1:54321", "http://localhost:54321", "https://abc.supabase.co"]
)
def test_supabase_url_allows_https_or_loopback_http(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    monkeypatch.setenv("SUPABASE_URL", url)

    assert Settings(_env_file=None).supabase_url == url


@pytest.mark.parametrize(
    "url", ["http://auth.example.com", "ftp://127.0.0.1", "127.0.0.1:54321", "http://10.0.0.5"]
)
def test_supabase_url_rejects_plain_http_to_remote_hosts(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    monkeypatch.setenv("SUPABASE_URL", url)

    with pytest.raises(ValidationError, match="supabase_url"):
        Settings(_env_file=None)


def test_gemini_defaults_to_the_stable_flash_lite_model_with_no_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)

    settings = Settings(_env_file=None)

    assert settings.gemini_model == "gemini-3.5-flash-lite"
    assert settings.gemini_api_key is None


def test_gemini_api_key_is_masked_in_repr_and_str(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key-123")

    settings = Settings(_env_file=None)

    assert settings.gemini_api_key is not None
    assert settings.gemini_api_key.get_secret_value() == "fake-gemini-key-123"
    assert "fake-gemini-key-123" not in repr(settings)
    assert "fake-gemini-key-123" not in str(settings)


def test_recovery_defaults_match_adr_0006(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    for name in (
        "AI_RECOVERY_ENABLED",
        "AI_RECOVERY_INTERVAL_SECONDS",
        "AI_RECOVERY_AGE_SECONDS",
        "AI_RECOVERY_BATCH_SIZE",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=None)

    assert settings.ai_recovery_enabled is True
    assert settings.ai_recovery_interval_seconds == 60
    assert settings.ai_recovery_age_seconds == 120
    assert settings.ai_recovery_batch_size == 10


@pytest.mark.parametrize(
    "name", ["AI_RECOVERY_INTERVAL_SECONDS", "AI_RECOVERY_AGE_SECONDS", "AI_RECOVERY_BATCH_SIZE"]
)
def test_recovery_numbers_must_be_positive(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    monkeypatch.setenv(name, "0")

    with pytest.raises(ValidationError, match=name.lower()):
        Settings(_env_file=None)
