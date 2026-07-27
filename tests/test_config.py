from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.config import Settings, load_dotenv, read_dotenv
from app.server import main


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

    def test_server_cli_overrides_preserve_all_loaded_settings(self) -> None:
        settings = Settings(
            db_path=Path("original.db"),
            host="127.0.0.1",
            port=8000,
            privacy_contact_email="privacy@example.test",
        )
        server = MagicMock()
        server.serve_forever.side_effect = KeyboardInterrupt
        with patch("app.server.load_settings", return_value=settings), patch(
            "app.server.serve", return_value=server
        ) as serve, patch("sys.argv", ["app.server", "--port", "18002"]):
            main()

        runtime_settings = serve.call_args.args[0]
        self.assertEqual(runtime_settings.port, 18002)
        self.assertEqual(runtime_settings.privacy_contact_email, "privacy@example.test")
        server.server_close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
