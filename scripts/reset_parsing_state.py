from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import load_settings
from app.database import Database
from app.utils import safe_json_dumps, safe_json_loads, utc_now


def _count(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row["count"] or 0)


def parsing_reset_counts(conn: Any) -> dict[str, int]:
    return {
        "raw_documents": _count(conn, "SELECT COUNT(*) AS count FROM raw_documents"),
        "plan_documents": _count(
            conn,
            "SELECT COUNT(*) AS count FROM collection_plan_documents WHERE raw_document_id IS NOT NULL",
        ),
        "expense_records": _count(conn, "SELECT COUNT(*) AS count FROM expense_records"),
        "candidates": _count(conn, "SELECT COUNT(*) AS count FROM restaurant_candidates"),
        "verifications": _count(conn, "SELECT COUNT(*) AS count FROM place_verifications"),
        "manual_tasks": _count(conn, "SELECT COUNT(*) AS count FROM manual_review_tasks"),
        "expense_links": _count(conn, "SELECT COUNT(*) AS count FROM restaurant_expense_links"),
        "linked_restaurants": _count(
            conn,
            "SELECT COUNT(DISTINCT restaurant_id) AS count FROM restaurant_expense_links",
        ),
        "restaurants": _count(conn, "SELECT COUNT(*) AS count FROM restaurants"),
        "reviews": _count(conn, "SELECT COUNT(*) AS count FROM restaurant_reviews"),
        "saved_restaurants": _count(
            conn,
            "SELECT COUNT(*) AS count FROM user_saved_restaurants",
        ),
        "admin_images": _count(conn, "SELECT COUNT(*) AS count FROM restaurant_admin_images"),
        "open_parse_dlq": _count(
            conn,
            """
            SELECT COUNT(*) AS count
            FROM dead_letter_queue
            WHERE status = 'open'
              AND stage IN ('collection_plan_parse', 'pending_verification')
            """,
        ),
    }


def reset_parsing_state(database: Database, *, apply: bool) -> dict[str, Any]:
    database.prepare()
    with database.session() as conn:
        running_jobs = _count(
            conn,
            "SELECT COUNT(*) AS count FROM batch_jobs WHERE status IN ('running', 'processing')",
        )
        if running_jobs:
            raise RuntimeError(f"cannot reset parsing while {running_jobs} batch job(s) are running")

        before = parsing_reset_counts(conn)
        if not apply:
            return {"status": "dry_run", "before": before}

        now = utc_now()
        conn.execute(
            """
            UPDATE restaurants
            SET place_verification_id = NULL,
                map_exposure_status = 'hidden',
                updated_at = ?
            WHERE id IN (
              SELECT DISTINCT restaurant_id
              FROM restaurant_expense_links
            )
            """,
            (now,),
        )
        conn.execute("DELETE FROM restaurant_expense_links")
        conn.execute("DELETE FROM manual_review_tasks")
        conn.execute("DELETE FROM place_verifications")
        conn.execute("DELETE FROM restaurant_candidates")
        conn.execute("DELETE FROM expense_records")

        for row in conn.execute("SELECT id, metadata_json FROM raw_documents").fetchall():
            metadata = safe_json_loads(row["metadata_json"], {})
            metadata.pop("parse_summary", None)
            conn.execute(
                "UPDATE raw_documents SET metadata_json = ? WHERE id = ?",
                (safe_json_dumps(metadata), int(row["id"])),
            )
        conn.execute(
            """
            UPDATE raw_documents
            SET status = 'collected',
                parse_status = 'not_requested',
                parsed_at = NULL,
                parse_error_message = NULL
            """
        )
        conn.execute(
            """
            UPDATE collection_plan_documents
            SET parse_status = 'not_requested',
                parse_attempts = 0,
                parse_error_message = NULL,
                parsed_at = NULL,
                rows_seen = 0,
                rows_inserted = 0,
                updated_at = ?
            WHERE raw_document_id IS NOT NULL
            """,
            (now,),
        )
        conn.execute(
            """
            UPDATE collection_plans
            SET rows_seen = 0,
                rows_inserted = 0,
                summary_json = ?,
                updated_at = ?
            """,
            (safe_json_dumps({"parse_reset": True, "reset_at": now}), now),
        )
        conn.execute(
            """
            UPDATE dead_letter_queue
            SET status = 'resolved',
                resolved_at = ?
            WHERE status = 'open'
              AND stage IN ('collection_plan_parse', 'pending_verification')
            """,
            (now,),
        )

        after = parsing_reset_counts(conn)
        expected_zero = (
            "expense_records",
            "candidates",
            "verifications",
            "manual_tasks",
            "expense_links",
            "linked_restaurants",
            "open_parse_dlq",
        )
        remaining = {key: after[key] for key in expected_zero if after[key] != 0}
        if remaining:
            raise RuntimeError(f"parsing reset postcondition failed: {remaining}")
        if after["restaurants"] != before["restaurants"]:
            raise RuntimeError("restaurant records changed during parsing reset")
        if after["reviews"] != before["reviews"]:
            raise RuntimeError("restaurant reviews changed during parsing reset")
        if after["saved_restaurants"] != before["saved_restaurants"]:
            raise RuntimeError("saved restaurants changed during parsing reset")
        if after["admin_images"] != before["admin_images"]:
            raise RuntimeError("restaurant images changed during parsing reset")

        raw_pending = _count(
            conn,
            "SELECT COUNT(*) AS count FROM raw_documents WHERE parse_status = 'not_requested'",
        )
        plan_pending = _count(
            conn,
            """
            SELECT COUNT(*) AS count
            FROM collection_plan_documents
            WHERE raw_document_id IS NOT NULL
              AND parse_status = 'not_requested'
            """,
        )
        if raw_pending != before["raw_documents"]:
            raise RuntimeError("not all raw documents returned to parsing pending")
        if plan_pending != before["plan_documents"]:
            raise RuntimeError("not all collection plan documents returned to parsing pending")

        return {
            "status": "reset",
            "reset_at": now,
            "before": before,
            "after": after,
            "parse_pending": {
                "raw_documents": raw_pending,
                "plan_documents": plan_pending,
            },
        }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reset parsed data while preserving collected source documents and user restaurant data."
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
    database = Database(settings.database_url)
    result = reset_parsing_state(database, apply=args.apply)
    if args.backup is not None:
        result["backup"] = str(args.backup.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
