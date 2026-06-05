from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.config import load_dotenv, read_dotenv


class ConfigTests(unittest.TestCase):
    def test_read_dotenv_parses_basic_values_and_quotes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                """
                # ignored
                APP_PORT=9000
                NAVER_SEARCH_CLIENT_ID="client-id"
                EMPTY=
                INVALID_LINE
                """,
                encoding="utf-8",
            )

            values = read_dotenv(path)

        self.assertEqual(values["APP_PORT"], "9000")
        self.assertEqual(values["NAVER_SEARCH_CLIENT_ID"], "client-id")
        self.assertEqual(values["EMPTY"], "")
        self.assertNotIn("INVALID_LINE", values)

    def test_load_dotenv_does_not_override_existing_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("APP_PORT=9000\nAPP_HOST=0.0.0.0\n", encoding="utf-8")
            environ = {"APP_PORT": "8000"}

            loaded = load_dotenv(path, environ=environ)

        self.assertEqual(loaded["APP_PORT"], "9000")
        self.assertEqual(environ["APP_PORT"], "8000")
        self.assertEqual(environ["APP_HOST"], "0.0.0.0")


if __name__ == "__main__":
    unittest.main()
