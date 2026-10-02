import sys


def test_runs_on_python_3_12() -> None:
    assert sys.version_info[:2] == (3, 12)
