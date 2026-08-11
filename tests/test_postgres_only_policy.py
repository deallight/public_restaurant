from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_PATHS = (
    PROJECT_ROOT / "app",
    PROJECT_ROOT / "scripts",
)
RUNTIME_FILES = (
    PROJECT_ROOT / ".env.example",
    PROJECT_ROOT / "requirements.txt",
)
FORBIDDEN_RUNTIME_TOKENS = (
    "".join(("sql", "ite3")),
    "".join(("sql", "ite://")),
    "APP_" + "DB_PATH",
)
FORBIDDEN_FILE_SUFFIXES = (
    ".db",
    "".join((".sql", "ite")),
    "".join((".sql", "ite3")),
)


class PostgresOnlyPolicyTests(unittest.TestCase):
    def test_runtime_has_no_embedded_database_backend(self) -> None:
        violations: list[str] = []
        candidates = list(RUNTIME_FILES)
        for runtime_path in RUNTIME_PATHS:
            candidates.extend(runtime_path.rglob("*.py"))

        for path in candidates:
            content = path.read_text(encoding="utf-8").lower()
            for token in FORBIDDEN_RUNTIME_TOKENS:
                if token.lower() in content:
                    violations.append(f"{path.relative_to(PROJECT_ROOT)}: {token}")

        self.assertEqual(violations, [])

    def test_repository_has_no_embedded_database_files(self) -> None:
        violations = [
            str(path.relative_to(PROJECT_ROOT))
            for path in PROJECT_ROOT.rglob("*")
            if path.is_file()
            and ".git" not in path.parts
            and path.name.lower().endswith(FORBIDDEN_FILE_SUFFIXES)
        ]

        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
