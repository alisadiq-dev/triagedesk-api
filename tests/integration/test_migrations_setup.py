from alembic import command
from alembic.config import Config


def alembic_config(database_url: str) -> Config:
    config = Config("alembic.ini")
    config.attributes["database_url"] = database_url
    return config


def test_upgrade_head_runs_on_a_fresh_database(test_database_url: str) -> None:
    command.upgrade(alembic_config(test_database_url), "head")
