from __future__ import annotations

import os
import stat
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "prepare_n150_move.sh"


class N150MoveScriptTests(unittest.TestCase):
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

    def test_help_describes_safe_move_workflow(self) -> None:
        result = subprocess.run(
            [str(SCRIPT), "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        normalized = " ".join(result.stdout.split())
        self.assertIn("create and verify a PostgreSQL backup", normalized)
        self.assertIn("stage DHCP for the next boot", normalized)
        self.assertIn("never runs `netplan apply`", normalized)

    def test_depart_orders_backup_and_network_before_poweroff(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        depart_source = source[source.index("depart() {") :]
        first_running_jobs = depart_source.index("check_no_running_jobs")
        stop_worker = depart_source.index('systemctl stop "$WORKER_SERVICE"')
        second_running_jobs = depart_source.index(
            "check_no_running_jobs", first_running_jobs + 1
        )
        backup = depart_source.index("create_verified_backup")
        dhcp = depart_source.index("stage_dhcp_for_next_boot")
        stop_web = depart_source.index('systemctl stop "$APP_SERVICE"')
        poweroff = depart_source.index("systemctl poweroff")

        self.assertLess(first_running_jobs, stop_worker)
        self.assertLess(stop_worker, second_running_jobs)
        self.assertLess(second_running_jobs, backup)
        self.assertLess(backup, dhcp)
        self.assertLess(dhcp, stop_web)
        self.assertLess(stop_web, poweroff)
        self.assertNotIn("netplan apply", depart_source)
        self.assertIn("pg_restore --list", source)
        self.assertIn("sha256sum", source)
        self.assertIn("restore_netplan_from_state", source)
        self.assertIn("oauth_lifecycle_jobs WHERE status = 'running'", source)

    @unittest.skipIf(os.geteuid() == 0, "non-root guard requires a non-root test process")
    def test_depart_requires_sudo(self) -> None:
        result = subprocess.run(
            [str(SCRIPT), "depart"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("run this command with sudo", result.stderr)


if __name__ == "__main__":
    unittest.main()
