from __future__ import annotations

import argparse
import os
import socket
import time
from typing import Any

from .config import Settings, load_settings
from .http_server import PublicRestaurantApplication
from .operation_jobs import OperationJobQueue
from .progress import VerificationProgressStore


JOB_LABELS = {
    "collection_plan_create": "목록 가져오기",
    "collection_plan_run": "수집",
    "collection_plan_retry": "재수집",
    "collection_plan_parse": "파싱",
    "collection_plan_parse_retry": "실패 재파싱",
    "verify_pending": "검증 대기 검증",
    "verify_collected": "수집 항목 검증",
}


class PersistedVerificationProgress:
    def __init__(self, queue: OperationJobQueue, job_id: int):
        self.queue = queue
        self.job_id = job_id
        self.store = VerificationProgressStore()

    def update(self, event: dict[str, Any]) -> None:
        self.store.update(event)
        if self.queue.database.backend == "postgresql":
            self.flush()

    def flush(self) -> None:
        self.queue.update_progress(self.job_id, self.store.snapshot())


class OperationWorker:
    def __init__(self, settings: Settings, worker_id: str = ""):
        self.app = PublicRestaurantApplication(settings)
        self.queue = self.app.operation_jobs
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}"

    def recover_interrupted_jobs(self) -> int:
        return self.queue.requeue_running()

    def run_once(self) -> dict[str, Any] | None:
        job = self.queue.claim_next(self.worker_id)
        if job is None:
            return None
        job_id = int(job["job_id"])
        label = JOB_LABELS.get(str(job["job_type"]), str(job["job_type"]))
        self.queue.update_progress(
            job_id,
            {
                "active": True,
                "stage": "starting",
                "label": f"{label} 시작",
                "updated_at": job["updated_at"],
            },
        )
        try:
            result = self._execute(job)
        except Exception as exc:
            return self.queue.fail(job_id, str(exc))
        return self.queue.succeed(job_id, result)

    def run_forever(self, poll_interval: float = 1.0) -> None:
        self.recover_interrupted_jobs()
        while True:
            job = self.run_once()
            if job is None:
                time.sleep(max(0.1, float(poll_interval)))

    def _execute(self, job: dict[str, Any]) -> dict[str, Any]:
        job_type = str(job["job_type"])
        payload = dict(job.get("payload") or {})
        plan_id = int(payload.get("plan_id") or 0)
        batch_size = int(payload["batch_size"]) if payload.get("batch_size") else None
        max_batches = int(payload.get("max_batches", 100) or 100)
        repeat = bool(payload.get("repeat", True))

        if job_type == "collection_plan_create":
            return self.app.create_collection_plan(
                start_date=str(payload.get("start_date", "")),
                end_date=str(payload.get("end_date", "")),
                batch_size=int(payload.get("batch_size", 20) or 20),
            )
        if job_type == "collection_plan_run":
            if repeat:
                return self.app.run_collection_plan_batches(
                    plan_id,
                    batch_size=batch_size,
                    max_batches=max_batches,
                )
            return self.app.run_collection_plan_batch(plan_id, batch_size=batch_size)
        if job_type == "collection_plan_retry":
            return self.app.retry_collection_plan_failures(
                plan_id,
                batch_size=batch_size,
                max_batches=max_batches,
            )
        if job_type == "collection_plan_parse":
            if repeat:
                return self.app.parse_collection_plan_batches(
                    plan_id,
                    batch_size=batch_size,
                    max_batches=max_batches,
                )
            return self.app.parse_collection_plan_batch(plan_id, batch_size=batch_size)
        if job_type == "collection_plan_parse_retry":
            return self.app.retry_collection_plan_parse_failures(
                plan_id,
                batch_size=batch_size,
                max_batches=max_batches,
            )
        if job_type in {"verify_pending", "verify_collected"}:
            progress = PersistedVerificationProgress(self.queue, int(job["job_id"]))
            arguments = {
                "limit": int(payload.get("limit", 100) or 100),
                "sort": str(payload.get("sort", "verification_oldest")),
                "progress_callback": progress.update,
            }
            if job_type == "verify_pending":
                result = self.app.verify_pending(**arguments)
            else:
                result = self.app.verify_collected(**arguments)
            progress.flush()
            return result
        raise RuntimeError(f"unsupported operation job type: {job_type}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run queued collection, parsing, and verification jobs.")
    parser.add_argument("--once", action="store_true", help="process at most one queued job and exit")
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--worker-id", default="")
    args = parser.parse_args()
    worker = OperationWorker(load_settings(), worker_id=args.worker_id)
    if args.once:
        worker.run_once()
        return
    worker.run_forever(poll_interval=args.poll_interval)


if __name__ == "__main__":
    main()
