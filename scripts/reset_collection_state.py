from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import load_settings
from app.database import Database
from app.utils import safe_json_dumps, utc_now


def _count(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row["count"] or 0)


def collection_reset_counts(conn: Any) -> dict[str, int]:
    return {
        "raw_documents": _count(conn, "SELECT COUNT(*) AS count FROM raw_documents"),
        "plan_documents": _count(conn, "SELECT COUNT(*) AS count FROM collection_plan_documents"),
        "plan_documents_with_raw": _count(
            conn,
            "SELECT COUNT(*) AS count FROM collection_plan_documents WHERE raw_document_id IS NOT NULL",
        ),
        "collection_plans": _count(conn, "SELECT COUNT(*) AS count FROM collection_plans"),
        "expense_records": _count(conn, "SELECT COUNT(*) AS count FROM expense_records"),
        "candidates": _count(conn, "SELECT COUNT(*) AS count FROM restaurant_candidates"),
        "verifications": _count(conn, "SELECT COUNT(*) AS count FROM place_verifications"),
        "expense_links": _count(conn, "SELECT COUNT(*) AS count FROM restaurant_expense_links"),
        "restaurants": _count(conn, "SELECT COUNT(*) AS count FROM restaurants"),
        "reviews": _count(conn, "SELECT COUNT(*) AS count FROM restaurant_reviews"),
        "saved_restaurants": _count(conn, "SELECT COUNT(*) AS count FROM user_saved_restaurants"),
        "admin_images": _count(conn, "SELECT COUNT(*) AS count FROM restaurant_admin_images"),
        "open_collection_dlq": _count(
            conn,
            """
            SELECT COUNT(*) AS count
            FROM dead_letter_queue
            WHERE status = 'open' AND stage = 'collection_plan_document'
            """,
        ),
    }


def reset_collection_state(database: Database, *, apply: bool) -> dict[str, Any]:
    database.prepare()
    with database.session() as conn:
        running_jobs = _count(
            conn,
            "SELECT COUNT(*) AS count FROM batch_jobs WHERE status IN ('running', 'processing')",
        )
        if running_jobs:
            raise RuntimeError(f"cannot reset collection while {running_jobs} batch job(s) are running")

        before = collection_reset_counts(conn)
        downstream_tables = ("expense_records", "candidates", "verifications", "expense_links")
        downstream_rows = {key: before[key] for key in downstream_tables if before[key] != 0}
        if downstream_rows:
            raise RuntimeError(
                f"parsing state must be reset before collection state: {downstream_rows}"
            )
        if not apply:
            return {"status": "dry_run", "before": before}

        now = utc_now()
        conn.execute(
            """
            UPDATE collection_plan_documents
            SET status = 'pending',
                raw_document_id = NULL,
                batch_job_id = NULL,
                rows_seen = 0,
                rows_inserted = 0,
                attempts = 0,
                parse_status = 'not_requested',
                parse_attempts = 0,
                parse_error_message = NULL,
                parsed_at = NULL,
                error_message = NULL,
                metadata_json = '{}',
                updated_at = ?
            """,
            (now,),
        )
        conn.execute("DELETE FROM raw_documents")

        plans = conn.execute("SELECT id FROM collection_plans ORDER BY id").fetchall()
        for plan in plans:
            plan_id = int(plan["id"])
            document_count = _count(
                conn,
                "SELECT COUNT(*) AS count FROM collection_plan_documents WHERE plan_id = ?",
                (plan_id,),
            )
            status = "ready" if document_count else "empty"
            conn.execute(
                """
                UPDATE collection_plans
                SET status = ?,
                    discovered_count = ?,
                    pending_count = ?,
                    collected_count = 0,
                    duplicate_count = 0,
                    failed_count = 0,
                    rows_seen = 0,
                    rows_inserted = 0,
                    summary_json = ?,
                    updated_at = ?,
                    completed_at = NULL
                WHERE id = ?
                """,
                (
                    status,
                    document_count,
                    document_count,
                    safe_json_dumps({"collection_reset": True, "reset_at": now}),
                    now,
                    plan_id,
                ),
            )
        conn.execute(
            """
            UPDATE dead_letter_queue
            SET status = 'resolved',
                resolved_at = ?
            WHERE status = 'open' AND stage = 'collection_plan_document'
            """,
            (now,),
        )

        after = collection_reset_counts(conn)
        expected_zero = (
            "raw_documents",
            "plan_documents_with_raw",
            "expense_records",
            "candidates",
            "verifications",
            "expense_links",
            "open_collection_dlq",
        )
        remaining = {key: after[key] for key in expected_zero if after[key] != 0}
        if remaining:
            raise RuntimeError(f"collection reset postcondition failed: {remaining}")
        for key in (
            "plan_documents",
            "collection_plans",
            "restaurants",
            "reviews",
            "saved_restaurants",
            "admin_images",
        ):
            if after[key] != before[key]:
                raise RuntimeError(f"preserved row count changed during collection reset: {key}")

        pending_documents = _count(
            conn,
            "SELECT COUNT(*) AS count FROM collection_plan_documents WHERE status = 'pending'",
        )
        if pending_documents != before["plan_documents"]:
            raise RuntimeError("not all collection plan documents returned to pending")

        return {
            "status": "reset",
            "reset_at": now,
            "before": before,
            "after": after,
            "collection_pending": pending_documents,
        }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reset collection data while preserving discovered document lists and user data."
    )
    parser.add_argument("--apply", action="store_true", help="apply the reset; default is a dry-run")
    parser.add_argument(
        "--backup",
        type=Path,
        help="required with --apply; path to a validated pre-reset database backup",
    )
    args = parser.parse_args()
    if args.apply:
        if args.backup is None or not args.backup.is_file() or args.backup.stat().st_size <= 0:
            raise SystemExit("--apply requires an existing non-empty --backup file")

    settings = load_settings()
    database = Database(settings.database_url or settings.db_path)
    result = reset_collection_state(database, apply=args.apply)
    if args.backup is not None:
        result["backup"] = str(args.backup.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
