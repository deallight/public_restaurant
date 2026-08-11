from __future__ import annotations

from typing import Any, Iterable

from .database import Database
from .utils import safe_json_dumps, safe_json_loads, utc_now


ACTIVE_JOB_STATUSES = ("queued", "running")
TERMINAL_JOB_STATUSES = ("succeeded", "failed")
SUPPORTED_OPERATION_JOB_TYPES = {
    "collection_plan_create",
    "collection_plan_run",
    "collection_plan_retry",
    "collection_plan_parse",
    "collection_plan_parse_retry",
    "verify_pending",
    "verify_collected",
}


class OperationJobQueue:
    def __init__(self, database: Database):
        self.database = database

    def enqueue(
        self,
        job_type: str,
        payload: dict[str, Any],
        *,
        dedupe_key: str = "",
        requested_by: str = "",
    ) -> dict[str, Any]:
        normalized_type = str(job_type or "").strip()
        if normalized_type not in SUPPORTED_OPERATION_JOB_TYPES:
            raise ValueError(f"unsupported operation job type: {normalized_type}")
        normalized_dedupe_key = str(dedupe_key or "").strip()[:240]
        with self.database.session() as conn:
            existing = self._active_by_dedupe_key(conn, normalized_dedupe_key)
            if existing is not None:
                return {**self._public_payload(existing), "deduplicated": True}
            now = utc_now()
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO operation_jobs
                      (job_type, status, payload_json, dedupe_key, requested_by,
                       created_at, updated_at)
                    VALUES (?, 'queued', ?, ?, ?, ?, ?)
                    """,
                    (
                        normalized_type,
                        safe_json_dumps(payload),
                        normalized_dedupe_key,
                        str(requested_by or "")[:160],
                        now,
                        now,
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM operation_jobs WHERE id = ?",
                    (int(cursor.lastrowid),),
                ).fetchone()
            except Exception:
                existing = self._active_by_dedupe_key(conn, normalized_dedupe_key)
                if existing is None:
                    raise
                return {**self._public_payload(existing), "deduplicated": True}
        return {**self._public_payload(row), "deduplicated": False}

    def get(self, job_id: int) -> dict[str, Any] | None:
        with self.database.session() as conn:
            row = conn.execute(
                "SELECT * FROM operation_jobs WHERE id = ?",
                (int(job_id),),
            ).fetchone()
        return self._public_payload(row) if row is not None else None

    def list(
        self,
        *,
        statuses: Iterable[str] = (),
        job_types: Iterable[str] = (),
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        normalized_statuses = [str(value) for value in statuses if str(value)]
        normalized_types = [str(value) for value in job_types if str(value)]
        if normalized_statuses:
            clauses.append(
                "status IN (" + ",".join("?" for _ in normalized_statuses) + ")"
            )
            params.extend(normalized_statuses)
        if normalized_types:
            clauses.append(
                "job_type IN (" + ",".join("?" for _ in normalized_types) + ")"
            )
            params.extend(normalized_types)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(int(limit or 20), 100)))
        with self.database.session() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM operation_jobs
                {where}
                ORDER BY id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [self._public_payload(row) for row in rows]

    def latest(self, job_types: Iterable[str]) -> dict[str, Any] | None:
        rows = self.list(job_types=job_types, limit=1)
        return rows[0] if rows else None

    def claim_next(self, worker_id: str) -> dict[str, Any] | None:
        now = utc_now()
        with self.database.session() as conn:
            row = conn.execute(
                """
                UPDATE operation_jobs
                SET status = 'running',
                    worker_id = ?,
                    attempts = attempts + 1,
                    started_at = COALESCE(started_at, ?),
                    heartbeat_at = ?,
                    updated_at = ?
                WHERE id = (
                  SELECT id
                  FROM operation_jobs
                  WHERE status = 'queued'
                  ORDER BY id
                  LIMIT 1
                )
                  AND status = 'queued'
                RETURNING *
                """,
                (str(worker_id or "")[:160], now, now, now),
            ).fetchone()
        return self._public_payload(row) if row is not None else None

    def update_progress(self, job_id: int, progress: dict[str, Any]) -> None:
        now = utc_now()
        with self.database.session() as conn:
            conn.execute(
                """
                UPDATE operation_jobs
                SET progress_json = ?, heartbeat_at = ?, updated_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (safe_json_dumps(progress), now, now, int(job_id)),
            )

    def succeed(self, job_id: int, result: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with self.database.session() as conn:
            conn.execute(
                """
                UPDATE operation_jobs
                SET status = 'succeeded', result_json = ?, error_message = NULL,
                    heartbeat_at = ?, finished_at = ?, updated_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (safe_json_dumps(result), now, now, now, int(job_id)),
            )
            row = conn.execute(
                "SELECT * FROM operation_jobs WHERE id = ?",
                (int(job_id),),
            ).fetchone()
        return self._public_payload(row)

    def fail(self, job_id: int, error_message: str) -> dict[str, Any]:
        now = utc_now()
        with self.database.session() as conn:
            conn.execute(
                """
                UPDATE operation_jobs
                SET status = 'failed', error_message = ?, heartbeat_at = ?,
                    finished_at = ?, updated_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (str(error_message or "operation failed")[:2000], now, now, now, int(job_id)),
            )
            row = conn.execute(
                "SELECT * FROM operation_jobs WHERE id = ?",
                (int(job_id),),
            ).fetchone()
        return self._public_payload(row)

    def requeue_running(self) -> int:
        now = utc_now()
        with self.database.session() as conn:
            cursor = conn.execute(
                """
                UPDATE operation_jobs
                SET status = 'queued', worker_id = NULL, heartbeat_at = NULL,
                    progress_json = '{}', started_at = NULL, updated_at = ?
                WHERE status = 'running'
                """,
                (now,),
            )
            return int(cursor.rowcount)

    def _active_by_dedupe_key(self, conn: Any, dedupe_key: str) -> Any | None:
        if not dedupe_key:
            return None
        return conn.execute(
            """
            SELECT *
            FROM operation_jobs
            WHERE dedupe_key = ? AND status IN ('queued', 'running')
            ORDER BY id DESC
            LIMIT 1
            """,
            (dedupe_key,),
        ).fetchone()

    def _public_payload(self, row: Any) -> dict[str, Any]:
        payload = dict(row)
        payload["job_id"] = int(payload.pop("id"))
        payload["payload"] = safe_json_loads(payload.pop("payload_json", "{}"), {})
        payload["progress"] = safe_json_loads(payload.pop("progress_json", "{}"), {})
        payload["result"] = safe_json_loads(payload.pop("result_json", "{}"), {})
        payload["status_url"] = f"/ops/jobs/{payload['job_id']}"
        return payload
