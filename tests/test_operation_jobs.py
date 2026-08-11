from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import Settings
from app.database import Database
from app.operation_jobs import OperationJobQueue
from app.worker import OperationWorker
from tests.postgres_test import fresh_postgres_database, postgres_test_url


class OperationJobQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = fresh_postgres_database()
        self.queue = OperationJobQueue(self.database)

    def test_job_lifecycle_is_persisted_and_duplicate_active_request_is_reused(self) -> None:
        first = self.queue.enqueue(
            "collection_plan_run",
            {"plan_id": 7, "repeat": True},
            dedupe_key="collection-plan-7",
            requested_by="user:1",
        )
        duplicate = self.queue.enqueue(
            "collection_plan_run",
            {"plan_id": 7, "repeat": True},
            dedupe_key="collection-plan-7",
            requested_by="user:1",
        )

        self.assertEqual(first["status"], "queued")
        self.assertEqual(duplicate["job_id"], first["job_id"])
        self.assertTrue(duplicate["deduplicated"])

        running = self.queue.claim_next("test-worker")
        self.assertEqual(running["job_id"], first["job_id"])
        self.assertEqual(running["status"], "running")
        self.assertEqual(running["attempts"], 1)

        self.queue.update_progress(first["job_id"], {"stage": "collecting", "processed": 3})
        completed = self.queue.succeed(first["job_id"], {"status": "success", "summary": {"documents_seen": 3}})

        self.assertEqual(completed["status"], "succeeded")
        self.assertEqual(completed["progress"]["processed"], 3)
        self.assertEqual(completed["result"]["summary"]["documents_seen"], 3)
        self.assertIsNotNone(completed["finished_at"])

        next_job = self.queue.enqueue(
            "collection_plan_run",
            {"plan_id": 7, "repeat": True},
            dedupe_key="collection-plan-7",
        )
        self.assertNotEqual(next_job["job_id"], first["job_id"])

    def test_running_job_can_be_requeued_after_worker_restart(self) -> None:
        queued = self.queue.enqueue("verify_collected", {"limit": 20})
        self.queue.claim_next("stopped-worker")

        self.assertEqual(self.queue.requeue_running(), 1)
        recovered = self.queue.get(queued["job_id"])

        self.assertEqual(recovered["status"], "queued")
        self.assertIsNone(recovered["worker_id"])

    def test_worker_dispatches_collection_job_and_stores_result(self) -> None:
        settings = Settings(database_url=postgres_test_url())
        worker = OperationWorker(settings, worker_id="worker-test")
        queued = worker.queue.enqueue(
            "collection_plan_run",
            {"plan_id": 42, "batch_size": 17, "repeat": False, "max_batches": 1},
        )
        result = {"status": "success", "summary": {"documents_seen": 17}}

        with patch.object(worker.app, "run_collection_plan_batch", return_value=result) as run:
            completed = worker.run_once()

        run.assert_called_once_with(42, batch_size=17)
        self.assertEqual(completed["job_id"], queued["job_id"])
        self.assertEqual(completed["status"], "succeeded")
        self.assertEqual(completed["result"], result)

    def test_worker_dispatches_parsing_job_and_stores_result(self) -> None:
        settings = Settings(database_url=postgres_test_url())
        worker = OperationWorker(settings, worker_id="parsing-worker-test")
        queued = worker.queue.enqueue(
            "collection_plan_parse",
            {"plan_id": 51, "batch_size": 23, "repeat": True, "max_batches": 4},
        )
        result = {"status": "success", "summary": {"documents_parsed": 23}}

        with patch.object(worker.app, "parse_collection_plan_batches", return_value=result) as run:
            completed = worker.run_once()

        run.assert_called_once_with(51, batch_size=23, max_batches=4)
        self.assertEqual(completed["job_id"], queued["job_id"])
        self.assertEqual(completed["status"], "succeeded")
        self.assertEqual(completed["result"], result)

    def test_worker_persists_verification_progress_for_http_polling(self) -> None:
        settings = Settings(database_url=postgres_test_url())
        worker = OperationWorker(settings, worker_id="verification-worker-test")
        queued = worker.queue.enqueue("verify_collected", {"limit": 1, "sort": "id_asc"})

        def verify(*, limit: int, sort: str, progress_callback):
            self.assertEqual((limit, sort), (1, "id_asc"))
            progress_callback({"event": "batch_started", "batch_id": 9, "total": 1, "limit": 1})
            progress_callback(
                {
                    "event": "candidate_done",
                    "batch_id": 9,
                    "candidate_id": 12,
                    "order": 1,
                    "status": "completed",
                    "decision": "approved",
                    "percent": 100,
                }
            )
            progress_callback(
                {
                    "event": "batch_finished",
                    "batch_id": 9,
                    "processed": 1,
                    "summary": {"approved": 1, "needs_review": 0, "rejected": 0, "dlq": 0},
                }
            )
            return {"status": "success", "summary": {"rows_processed": 1, "approved": 1}}

        with patch.object(worker.app, "verify_collected", side_effect=verify):
            worker.run_once()

        completed = worker.queue.get(queued["job_id"])
        self.assertEqual(completed["status"], "succeeded")
        self.assertFalse(completed["progress"]["active"])
        self.assertEqual(completed["progress"]["processed"], 1)
        self.assertEqual(completed["progress"]["classifications"]["approved"], 1)

    def test_worker_cli_processes_job_in_a_separate_process(self) -> None:
        database_url = postgres_test_url()
        database = Database(database_url)
        queue = OperationJobQueue(database)
        queued = queue.enqueue("verify_collected", {"limit": 1, "sort": "id_asc"})
        environment = {
            **os.environ,
            "APP_ENV": "development",
            "DATABASE_URL": database_url,
        }

        result = subprocess.run(
            [sys.executable, "-m", "app.worker", "--once", "--worker-id", "subprocess-test"],
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        completed = queue.get(queued["job_id"])
        self.assertEqual(completed["status"], "succeeded")
        self.assertEqual(completed["result"]["status"], "success")


if __name__ == "__main__":
    unittest.main()
