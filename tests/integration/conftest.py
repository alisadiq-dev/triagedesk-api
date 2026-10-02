import os

import pytest
from pydantic import SecretStr

from app.core.config import Settings

# Throwaway local container from docker-compose.yml (not a real credential).
DEFAULT_TEST_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/triagedesk_test"


@pytest.fixture
def test_database_url() -> str:
    return os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)


@pytest.fixture
def settings(test_database_url: str) -> Settings:
    return Settings(_env_file=None, database_url=SecretStr(test_database_url))
