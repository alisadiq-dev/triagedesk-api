"""Pre-commit and CI check for CONSTRAINTS.md: no new suppressions, no .env files."""

import re
import sys
from pathlib import Path

SUPPRESSION = re.compile(r"#\s*noqa|#\s*type:\s*ignore|pytest\.mark\.(skip|xfail)|pytest\.skip\(")
ENV_FILE = re.compile(r"(^|/)\.env(\..+)?$")


def find_violations(source: str) -> list[tuple[int, str]]:
    return [
        (number, line.strip())
        for number, line in enumerate(source.splitlines(), start=1)
        if SUPPRESSION.search(line)
    ]


def is_forbidden_env_file(path: str) -> bool:
    return bool(ENV_FILE.search(path)) and not path.endswith(".env.example")


def main(paths: list[str]) -> int:
    failed = False
    for path in paths:
        if is_forbidden_env_file(path):
            print(f"{path}: .env files must never be committed")
            failed = True
        if path.endswith(".py"):
            for number, line in find_violations(Path(path).read_text()):
                print(f"{path}:{number}: suppression needs owner approval: {line}")
                failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
