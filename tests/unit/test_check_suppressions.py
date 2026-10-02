from scripts.check_suppressions import find_violations, is_forbidden_env_file

NOQA = "x = 1  # no" + "qa: E501"
TYPE_IGNORE = "y = f()  # type: " + "ignore[arg-type]"
SKIP = "@pytest.mark." + "skip(reason='later')"
XFAIL = "@pytest.mark." + "xfail"
SKIP_CALL = "pytest." + "skip('later')"


def test_flags_each_kind_of_suppression() -> None:
    for line in (NOQA, TYPE_IGNORE, SKIP, XFAIL, SKIP_CALL):
        assert find_violations(line + "\n"), line


def test_reports_line_numbers() -> None:
    source = "ok = 1\n" + NOQA + "\n"

    assert find_violations(source) == [(2, NOQA)]


def test_clean_code_has_no_violations() -> None:
    assert find_violations("def f() -> int:\n    return 1\n") == []


def test_blocks_env_files_but_not_the_example() -> None:
    assert is_forbidden_env_file(".env")
    assert is_forbidden_env_file("deploy/.env.production")
    assert not is_forbidden_env_file(".env.example")
    assert not is_forbidden_env_file("app/environment.py")
