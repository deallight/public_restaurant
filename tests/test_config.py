from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.config import Settings, load_dotenv, read_dotenv
from app.server import ROOT_DIR, _start_local_worker, _stop_local_worker, main


class ConfigTests(unittest.TestCase):
    def test_local_worker_uses_current_python_and_project_root(self) -> None:
        worker_process = MagicMock()
        with patch("app.server.subprocess.Popen", return_value=worker_process) as popen:
            result = _start_local_worker(0)

        self.assertIs(result, worker_process)
        popen.assert_called_once_with(
            [
                sys.executable,
                "-m",
                "app.worker",
                "--poll-interval",
                "0.1",
            ],
            cwd=ROOT_DIR,
            start_new_session=True,
        )

    def test_active_local_worker_is_stopped_with_server(self) -> None:
        worker_process = MagicMock()
        worker_process.poll.return_value = None

        _stop_local_worker(worker_process)

        worker_process.terminate.assert_called_once_with()
        worker_process.wait.assert_called_once_with(timeout=5)
        worker_process.kill.assert_not_called()

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
            database_url="postgresql://restaurant_app@127.0.0.1/example_test",
            host="127.0.0.1",
            port=8000,
            privacy_contact_email="privacy@example.test",
        )
        server = MagicMock()
        worker_process = MagicMock()
        server.serve_forever.side_effect = KeyboardInterrupt
        with patch("app.server.load_settings", return_value=settings), patch(
            "app.server.serve", return_value=server
        ) as serve, patch(
            "app.server._start_local_worker", return_value=worker_process
        ) as start_worker, patch(
            "app.server._stop_local_worker"
        ) as stop_worker, patch("sys.argv", ["app.server", "--port", "18002"]):
            main()

        runtime_settings = serve.call_args.args[0]
        self.assertEqual(runtime_settings.port, 18002)
        self.assertEqual(runtime_settings.privacy_contact_email, "privacy@example.test")
        start_worker.assert_called_once_with(1.0)
        stop_worker.assert_called_once_with(worker_process)
        server.server_close.assert_called_once_with()

    def test_production_server_does_not_start_local_worker(self) -> None:
        settings = Settings(
            database_url="postgresql://restaurant_app@127.0.0.1/example_test",
            app_env="production",
            privacy_contact_email="privacy@example.test",
        )
        server = MagicMock()
        server.serve_forever.side_effect = KeyboardInterrupt
        with patch("app.server.load_settings", return_value=settings), patch(
            "app.server.serve", return_value=server
        ), patch("app.server._start_local_worker") as start_worker, patch(
            "sys.argv", ["app.server"]
        ):
            main()

        start_worker.assert_not_called()
        server.server_close.assert_called_once_with()

    def test_no_worker_flag_disables_local_worker(self) -> None:
        settings = Settings(
            database_url="postgresql://restaurant_app@127.0.0.1/example_test",
            app_env="development",
        )
        server = MagicMock()
        server.serve_forever.side_effect = KeyboardInterrupt
        with patch("app.server.load_settings", return_value=settings), patch(
            "app.server.serve", return_value=server
        ), patch("app.server._start_local_worker") as start_worker, patch(
            "sys.argv", ["app.server", "--no-worker"]
        ):
            main()

        start_worker.assert_not_called()
        server.server_close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
