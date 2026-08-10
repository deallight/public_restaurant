from __future__ import annotations

import os
import stat
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "deploy_n150.sh"


class N150DeployScriptTests(unittest.TestCase):
    def test_script_is_executable_and_has_valid_bash_syntax(self) -> None:
        self.assertTrue(SCRIPT.exists())
        self.assertTrue(SCRIPT.stat().st_mode & stat.S_IXUSR)
        result = subprocess.run(
            ["bash", "-n", str(SCRIPT)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_help_describes_safe_default_workflow(self) -> None:
        result = subprocess.run(
            [str(SCRIPT), "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        normalized_help = " ".join(result.stdout.split())
        self.assertIn("isolated PostgreSQL integration suite", normalized_help)
        self.assertIn("automatically restores the previous release", normalized_help)
        self.assertIn("--skip-tests", normalized_help)

    def test_safety_gates_precede_the_release_switch(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        schema_check = source.index("-m scripts.check_db_schema")
        backup = source.index("pg_dump --format=custom")
        preflight = source.index('wait_for_http 200 "http://${APP_HOST}:${PREFLIGHT_PORT}/"')
        release_switch = source.index('atomic_switch "$NEW_RELEASE"')
        public_check = source.index('wait_for_http 200 "${PUBLIC_URL}/" "public home"')

        self.assertLess(schema_check, backup)
        self.assertLess(backup, preflight)
        self.assertLess(preflight, release_switch)
        self.assertLess(release_switch, public_check)
        self.assertIn("rollback_release", source)
        self.assertIn("another deployment is already running", source)

    @unittest.skipIf(os.geteuid() == 0, "non-root guard requires a non-root test process")
    def test_deployment_requires_sudo(self) -> None:
        result = subprocess.run(
            [str(SCRIPT)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("run this script with sudo", result.stderr)


if __name__ == "__main__":
    unittest.main()
