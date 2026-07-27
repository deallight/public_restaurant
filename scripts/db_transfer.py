from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from app.database import Database, SQLITE_TABLES


TRANSFER_TABLES = list(reversed(SQLITE_TABLES))
CORE_TABLES = [
    "regions",
    "institutions",
    "raw_documents",
    "expense_records",
    "restaurant_candidates",
    "restaurants",
    "restaurant_expense_links",
    "manual_review_tasks",
    "user_saved_restaurants",
    "restaurant_reviews",
]


def transfer_succeeded(result: dict[str, Any]) -> bool:
    return result.get("status") in {"dry_run", "applied"} and not result.get("errors")


def require_sqlite_copy(path: Path) -> None:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ValueError("SQLite source copy does not exist")
    if resolved.name == "public_restaurant.db" and resolved.parent.name == "var":
        raise ValueError("refusing the live SQLite path; make and use a backup copy")


def table_columns(conn: Any, table: str, backend: str) -> list[str]:
    if backend == "sqlite":
        return [str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")]
    return [
        str(row["column_name"])
        for row in conn.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = ?
            ORDER BY ordinal_position
            """,
            (table,),
        )
    ]


def transfer_key_columns(table: str, columns: Iterable[str]) -> list[str]:
    available = set(columns)
    if "id" in available:
        return ["id"]
    if table == "user_saved_restaurants":
        return ["user_id", "restaurant_id"]
    if "restaurant_id" in available:
        return ["restaurant_id"]
    raise ValueError(f"no transfer key configured for {table}")


def schema_diff(source: Database, target: Database) -> dict[str, dict[str, list[str]]]:
    differences: dict[str, dict[str, list[str]]] = {}
    with source.session() as source_conn, target.session() as target_conn:
        for table in TRANSFER_TABLES:
            source_columns = set(table_columns(source_conn, table, source.backend))
            target_columns = set(table_columns(target_conn, table, target.backend))
            missing = sorted(source_columns - target_columns)
            extra = sorted(target_columns - source_columns)
            if missing or extra:
                differences[table] = {"missing_in_target": missing, "extra_in_target": extra}
    return differences


def transfer(
    source: Database,
    target: Database,
    apply: bool = False,
    batch_size: int = 500,
) -> dict[str, Any]:
    safe_batch_size = max(1, min(int(batch_size or 500), 10_000))
    differences = schema_diff(source, target)
    blocking = {table: diff for table, diff in differences.items() if diff["missing_in_target"]}
    if blocking:
        return {"status": "schema_mismatch", "schema_diff": blocking, "tables": {}}
    result: dict[str, Any] = {
        "status": "applied" if apply else "dry_run",
        "tables": {},
        "errors": [],
    }
    with source.session() as source_conn, target.session() as target_conn:
        for table in TRANSFER_TABLES:
            target_columns = set(table_columns(target_conn, table, target.backend))
            source_columns = table_columns(source_conn, table, source.backend)
            columns = [column for column in source_columns if column in target_columns]
            key_columns = transfer_key_columns(table, columns)
            order_by = ", ".join(key_columns)
            source_cursor = source_conn.execute(
                f"SELECT * FROM {table} ORDER BY {order_by}"
            )
            source_count = 0
            written = 0
            names = ", ".join(columns)
            placeholders = ", ".join("?" for _ in columns)
            updates = ", ".join(
                f"{column} = excluded.{column}"
                for column in columns
                if column not in key_columns
            )
            conflict_target = ", ".join(key_columns)
            sql = (
                f"INSERT INTO {table} ({names}) VALUES ({placeholders}) "
                f"ON CONFLICT({conflict_target}) DO UPDATE SET {updates}"
            )
            while True:
                rows = source_cursor.fetchmany(safe_batch_size)
                if not rows:
                    break
                for row in rows:
                    source_count += 1
                    if not apply:
                        continue
                    try:
                        target_conn.execute("SAVEPOINT transfer_row")
                        cursor = target_conn.execute(sql, tuple(row[column] for column in columns))
                        written += max(0, cursor.rowcount)
                        target_conn.execute("RELEASE SAVEPOINT transfer_row")
                    except Exception as exc:
                        target_conn.execute("ROLLBACK TO SAVEPOINT transfer_row")
                        target_conn.execute("RELEASE SAVEPOINT transfer_row")
                        result["errors"].append(
                            {
                                "table": table,
                                "row": source_count,
                                "error_type": type(exc).__name__,
                            }
                        )
            if apply and target.backend == "postgresql" and key_columns == ["id"]:
                target_conn.execute(
                    f"""
                    SELECT setval(
                      pg_get_serial_sequence(?, 'id'),
                      COALESCE(MAX(id), 1),
                      MAX(id) IS NOT NULL
                    )
                    FROM {table}
                    """,
                    (table,),
                )
            result["tables"][table] = {"source": source_count, "written": written}
    if result["errors"]:
        result["status"] = "completed_with_errors"
    return result


def fingerprint(database: Database, tables: Iterable[str] = CORE_TABLES) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    with database.session() as conn:
        for table in tables:
            digest = hashlib.sha256()
            count = 0
            columns = table_columns(conn, table, database.backend)
            order_by = ", ".join(transfer_key_columns(table, columns))
            for row in conn.execute(f"SELECT * FROM {table} ORDER BY {order_by}"):
                canonical = json.dumps(dict(row), ensure_ascii=False, sort_keys=True, default=str)
                digest.update(canonical.encode("utf-8"))
                digest.update(b"\n")
                count += 1
            payload[table] = {"count": count, "sha256": digest.hexdigest()}
    return payload
