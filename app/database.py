from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Iterable

from .schema import SQLITE_SCHEMA
from .source_catalog import SourceCatalogEntry, iter_source_catalog
from .utils import safe_json_dumps, utc_now


SQLITE_TABLES = [
    "review_moderation_logs",
    "review_reports",
    "restaurant_reviews",
    "account_merge_requests",
    "oauth_accounts",
    "users",
    "decision_audit_logs",
    "entity_status_history",
    "alias_memory",
    "dead_letter_queue",
    "api_call_logs",
    "permit_snapshots",
    "manual_review_tasks",
    "restaurant_expense_links",
    "restaurants",
    "place_verifications",
    "restaurant_candidates",
    "expense_records",
    "collection_plan_documents",
    "raw_documents",
    "collection_plans",
    "batch_jobs",
    "source_registry",
    "institutions",
    "regions",
]


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.session() as conn:
            conn.executescript(SQLITE_SCHEMA)
            self._migrate_schema(conn)
            self.seed_core(conn)

    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        self._ensure_columns(
            conn,
            "restaurant_candidates",
            {
                "review_place_name": "TEXT",
                "review_normalized_place_name": "TEXT",
                "review_address": "TEXT",
                "review_normalized_address": "TEXT",
                "review_major_category": "TEXT",
            },
        )
        self._ensure_columns(
            conn,
            "collection_plans",
            {
                "created_batch_job_id": "INTEGER REFERENCES batch_jobs(id)",
            },
        )
        self._ensure_columns(
            conn,
            "collection_plan_documents",
            {
                "parse_status": "TEXT NOT NULL DEFAULT 'not_requested'",
                "parse_attempts": "INTEGER NOT NULL DEFAULT 0",
                "parse_error_message": "TEXT",
                "parsed_at": "TEXT",
            },
        )
        self._ensure_columns(
            conn,
            "raw_documents",
            {
                "parse_status": "TEXT NOT NULL DEFAULT 'not_requested'",
                "parsed_at": "TEXT",
                "parse_error_message": "TEXT",
            },
        )
        self._backfill_parse_status(conn)

    def _ensure_columns(
        self,
        conn: sqlite3.Connection,
        table_name: str,
        columns: dict[str, str],
    ) -> None:
        existing = {
            str(row["name"])
            for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        for column_name, column_sql in columns.items():
            if column_name not in existing:
                conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")

    def _backfill_parse_status(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            UPDATE raw_documents
            SET parse_status = 'parsed',
                parsed_at = COALESCE(parsed_at, collected_at)
            WHERE parse_status = 'not_requested'
              AND EXISTS (
                SELECT 1
                FROM expense_records er
                WHERE er.raw_document_id = raw_documents.id
              )
            """
        )
        conn.execute(
            """
            UPDATE collection_plan_documents
            SET parse_status = 'parsed',
                parsed_at = COALESCE(parsed_at, updated_at)
            WHERE parse_status = 'not_requested'
              AND rows_seen > 0
            """
        )
        conn.execute(
            """
            UPDATE collection_plan_documents
            SET parse_status = (
                  SELECT rd.parse_status
                  FROM raw_documents rd
                  WHERE rd.id = collection_plan_documents.raw_document_id
                ),
                parsed_at = COALESCE(
                  parsed_at,
                  (
                    SELECT rd.parsed_at
                    FROM raw_documents rd
                    WHERE rd.id = collection_plan_documents.raw_document_id
                  ),
                  updated_at
                )
            WHERE parse_status = 'not_requested'
              AND raw_document_id IS NOT NULL
              AND EXISTS (
                SELECT 1
                FROM raw_documents rd
                WHERE rd.id = collection_plan_documents.raw_document_id
                  AND rd.parse_status IN ('parsed', 'empty')
              )
            """
        )
        conn.execute(
            """
            UPDATE collection_plan_documents
            SET rows_seen = (
                  SELECT COUNT(*)
                  FROM expense_records er
                  WHERE er.raw_document_id = collection_plan_documents.raw_document_id
                )
            WHERE rows_seen = 0
              AND raw_document_id IS NOT NULL
              AND parse_status = 'parsed'
            """
        )

    def rollback_schema(self) -> None:
        """Drop the development SQLite schema in dependency order.

        This is intentionally explicit and is used by tests/local reset scripts only.
        Production PostgreSQL rollback should use reviewed SQL migrations/backups.
        """
        with self.session() as conn:
            conn.execute("PRAGMA foreign_keys = OFF")
            for table in SQLITE_TABLES:
                conn.execute(f"DROP TABLE IF EXISTS {table}")
            conn.execute("PRAGMA foreign_keys = ON")

    def seed_core(self, conn: sqlite3.Connection) -> None:
        region_id = self._seed_region(conn)
        for source in iter_source_catalog():
            self._seed_source(conn, region_id, source)

    def _seed_region(self, conn: sqlite3.Connection) -> int:
        region = conn.execute(
            "SELECT id FROM regions WHERE sido = ? AND sigungu IS NULL", ("부산광역시",)
        ).fetchone()
        if region is not None:
            return int(region["id"])
        cur = conn.execute(
            """
            INSERT INTO regions (sido, sigungu, region_code)
            VALUES (?, ?, ?)
            """,
            ("부산광역시", None, "26"),
        )
        return int(cur.lastrowid)

    def _seed_source(
        self,
        conn: sqlite3.Connection,
        region_id: int,
        source: SourceCatalogEntry,
    ) -> None:
        conn.execute(
            """
            INSERT OR IGNORE INTO institutions
              (region_id, name, institution_code, source_base_url, is_active)
            VALUES (?, ?, ?, ?, 1)
            """,
            (
                region_id,
                source.institution_name,
                source.institution_code,
                source.base_url,
            ),
        )
        conn.execute(
            """
            UPDATE institutions
            SET region_id = ?, name = ?, source_base_url = ?, is_active = 1
            WHERE institution_code = ?
            """,
            (region_id, source.institution_name, source.base_url, source.institution_code),
        )
        institution_id = conn.execute(
            "SELECT id FROM institutions WHERE institution_code = ?", (source.institution_code,)
        ).fetchone()["id"]
        config = {
            "priority": source.priority,
            "group_key": source.group_key,
            "group_label": source.group_label,
            "status": source.status,
            "expected_formats": list(source.expected_formats),
            "notes": source.notes,
        }
        conn.execute(
            """
            INSERT OR IGNORE INTO source_registry
              (institution_id, source_key, source_type, adapter_name, base_url, config_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                institution_id,
                source.source_key,
                source.source_type,
                source.adapter_name,
                source.base_url,
                safe_json_dumps(config),
            ),
        )
        conn.execute(
            """
            UPDATE source_registry
            SET institution_id = ?,
                source_type = ?,
                adapter_name = ?,
                base_url = ?,
                crawl_frequency = ?,
                is_active = 1,
                config_json = ?,
                updated_at = ?
            WHERE source_key = ?
            """,
            (
                institution_id,
                source.source_type,
                source.adapter_name,
                source.base_url,
                source.crawl_frequency,
                safe_json_dumps(config),
                utc_now(),
                source.source_key,
            ),
        )

    def count(self, table: str) -> int:
        with self.session() as conn:
            return int(conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"])

    def counts(self, tables: Iterable[str]) -> dict[str, int]:
        return {table: self.count(table) for table in tables}

    def create_batch(self, job_name: str) -> int:
        with self.session() as conn:
            cur = conn.execute(
                "INSERT INTO batch_jobs (job_name, status, started_at) VALUES (?, ?, ?)",
                (job_name, "running", utc_now()),
            )
            return int(cur.lastrowid)
