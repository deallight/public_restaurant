from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Iterable

from database.migrations.m0001_app_compatible import (
    DESCRIPTION as BASELINE_DESCRIPTION,
    MIGRATION_TABLE_STATEMENT,
    VERSION as BASELINE_VERSION,
    expected_schema_signature,
    statements,
)
from database.migrations.m0003_user_interactions import (
    DESCRIPTION as USER_INTERACTIONS_DESCRIPTION,
    STATEMENTS as USER_INTERACTIONS_STATEMENTS,
    VERSION as USER_INTERACTIONS_VERSION,
)
from database.migrations.m0004_operation_jobs import (
    DESCRIPTION as OPERATION_JOBS_DESCRIPTION,
    STATEMENTS as OPERATION_JOBS_STATEMENTS,
    VERSION as OPERATION_JOBS_VERSION,
)

from .db_compat import connect_postgres
from .schema import APP_SCHEMA
from .source_catalog import SourceCatalogEntry, iter_source_catalog
from .utils import safe_json_dumps, utc_now


APP_TABLES = [
    "operation_jobs",
    "review_moderation_logs",
    "review_reports",
    "review_reactions",
    "restaurant_user_images",
    "restaurant_admin_images",
    "restaurant_ai_summaries",
    "restaurant_reviews",
    "user_saved_restaurants",
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

REQUIRED_POSTGRES_MIGRATIONS = (
    (BASELINE_VERSION, BASELINE_DESCRIPTION, ()),
    (
        USER_INTERACTIONS_VERSION,
        USER_INTERACTIONS_DESCRIPTION,
        USER_INTERACTIONS_STATEMENTS,
    ),
    (
        OPERATION_JOBS_VERSION,
        OPERATION_JOBS_DESCRIPTION,
        OPERATION_JOBS_STATEMENTS,
    ),
)


class Database:
    def __init__(self, database_url: str):
        value = str(database_url).strip()
        if not value.startswith(("postgresql://", "postgres://")):
            raise ValueError("Database requires a PostgreSQL DATABASE_URL")
        self.database_url = value
        self.backend = "postgresql"

    def connect(self) -> Any:
        return connect_postgres(self.database_url)

    @contextmanager
    def session(self) -> Iterator[Any]:
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
        self._initialize_postgres()

    def prepare(self) -> None:
        """Prepare a runtime connection without applying PostgreSQL DDL."""
        self.verify_schema()

    def schema_issues(self, require_migration: bool = True) -> list[str]:
        with self.session() as conn:
            actual_rows = conn.execute(
                """
                SELECT table_name, column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                """
            ).fetchall()
        actual = {
            (str(item["table_name"]), str(item["column_name"])): str(item["data_type"])
            for item in actual_rows
        }
        issues: list[str] = []
        for table_name, columns in expected_schema_signature(postgres_schema_statements()).items():
            for column_name, expected_type in columns.items():
                actual_type = actual.get((table_name, column_name))
                if actual_type != expected_type:
                    issues.append(
                        f"{table_name}.{column_name}: expected {expected_type}, got {actual_type or 'missing'}"
                    )
        if require_migration and ("app_schema_migrations", "version") in actual:
            with self.session() as conn:
                applied_versions = {
                    str(row["version"])
                    for row in conn.execute(
                        "SELECT version FROM app_schema_migrations"
                    ).fetchall()
                }
            missing_versions = [
                version
                for version, _description, _statements in REQUIRED_POSTGRES_MIGRATIONS
                if version not in applied_versions
            ]
            issues[0:0] = [
                f"app_schema_migrations: version {version} is missing"
                for version in missing_versions
            ]
        elif require_migration:
            issues.insert(0, "app_schema_migrations: migration table is missing")
        return issues

    def verify_schema(self) -> None:
        try:
            issues = self.schema_issues(require_migration=True)
        except Exception as exc:
            raise RuntimeError(
                "PostgreSQL schema is not initialized; run scripts.init_db --apply after approval"
            ) from exc
        if issues:
            preview = "; ".join(issues[:5])
            raise RuntimeError(f"PostgreSQL schema is incompatible with required migrations: {preview}")

    def _initialize_postgres(self) -> None:
        with self.session() as conn:
            conn.execute(MIGRATION_TABLE_STATEMENT)
            applied_versions = {
                str(row["version"])
                for row in conn.execute(
                    "SELECT version FROM app_schema_migrations"
                ).fetchall()
            }
            if BASELINE_VERSION not in applied_versions:
                for statement in postgres_schema_statements():
                    conn.execute(statement)
                self._record_postgres_migration(
                    conn,
                    BASELINE_VERSION,
                    BASELINE_DESCRIPTION,
                )
            for (
                version,
                description,
                migration_statements,
            ) in REQUIRED_POSTGRES_MIGRATIONS[1:]:
                if version in applied_versions:
                    continue
                for statement in migration_statements:
                    conn.execute(statement)
                self._record_postgres_migration(conn, version, description)
            self._backfill_parse_status(conn)
            self.seed_core(conn)

    def _record_postgres_migration(
        self,
        conn: Any,
        version: str,
        description: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO app_schema_migrations (version, description)
            VALUES (?, ?)
            ON CONFLICT(version) DO NOTHING
            """,
            (version, description),
        )

    def _backfill_parse_status(self, conn: Any) -> None:
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

    def seed_core(self, conn: Any) -> None:
        region_id = self._seed_region(conn)
        for source in iter_source_catalog():
            self._seed_source(conn, region_id, source)

    def _seed_region(self, conn: Any) -> int:
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
        conn: Any,
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


def postgres_schema_statements() -> list[str]:
    """Build PostgreSQL DDL from the authoritative application schema."""
    return statements(APP_SCHEMA, APP_TABLES)
