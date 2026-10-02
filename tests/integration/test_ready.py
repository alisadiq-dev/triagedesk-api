from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.pool import QueuePool

from app.core.config import Settings
from app.core.db import build_engine
from app.main import create_app


def test_ready_returns_200_when_the_database_answers(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_ready_returns_503_in_the_error_format_when_the_database_is_unreachable() -> None:
    unreachable = Settings(
        _env_file=None,
        database_url=SecretStr("postgresql+asyncpg://user:leaky-pw@127.0.0.1:1/nope"),
        db_pool_timeout_seconds=2,
    )

    with TestClient(create_app(unreachable)) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "not_ready"
    assert "leaky-pw" not in response.text


def test_engine_applies_the_configured_pool_settings(settings: Settings) -> None:
    tuned = settings.model_copy(
        update={"db_pool_size": 3, "db_max_overflow": 2, "db_pool_timeout_seconds": 7}
    )

    pool = build_engine(tuned).pool

    assert isinstance(pool, QueuePool)
    assert pool.size() == 3
    assert pool.timeout() == 7
    assert pool._max_overflow == 2


def test_engine_hides_statement_parameters_so_errors_do_not_leak_personal_data(
    settings: Settings,
) -> None:
    engine = build_engine(settings)

    assert engine.sync_engine.hide_parameters is True
