from pathlib import Path

from app.core.config import Settings

ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"


def read_example() -> dict[str, str]:
    pairs: dict[str, str] = {}
    for line in ENV_EXAMPLE.read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            name, _, value = line.partition("=")
            pairs[name] = value
    return pairs


def test_env_example_lists_every_setting_the_app_reads() -> None:
    expected = {name.upper() for name in Settings.model_fields}

    assert set(read_example()) == expected


def test_env_example_holds_no_secret_values() -> None:
    assert read_example()["DATABASE_URL"] == ""
