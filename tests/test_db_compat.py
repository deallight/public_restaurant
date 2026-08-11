from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import load_settings
from app.database import (
    REQUIRED_POSTGRES_MIGRATIONS,
    Database,
    postgres_schema_statements,
)
from app.db_compat import postgres_sql
from database.migrations.m0001_app_compatible import expected_schema_signature
from database.migrations.m0003_user_interactions import (
    STATEMENTS as USER_INTERACTIONS_STATEMENTS,
    VERSION as USER_INTERACTIONS_VERSION,
)
from database.migrations.m0004_operation_jobs import (
    STATEMENTS as OPERATION_JOBS_STATEMENTS,
    VERSION as OPERATION_JOBS_VERSION,
)
from scripts.db_transfer import (
    fingerprint,
    require_sqlite_copy,
    transfer,
    transfer_key_columns,
    transfer_succeeded,
)


class DatabaseCompatibilityTests(unittest.TestCase):
    def test_review_reaction_uses_composite_transfer_key(self) -> None:
        self.assertEqual(
            transfer_key_columns(
                "review_reactions",
                ["review_id", "user_id", "reaction"],
            ),
            ["review_id", "user_id"],
        )

    def test_postgres_sql_translation(self) -> None:
        translated = postgres_sql(
            "INSERT OR IGNORE INTO items (name) VALUES (?)"
        )
        self.assertEqual(
            translated,
            "INSERT INTO items (name) VALUES (%s) ON CONFLICT DO NOTHING",
        )
        self.assertIn(
            "CURRENT_TIMESTAMP - INTERVAL '1 hour'",
            postgres_sql("SELECT datetime('now', '-1 hour')"),
        )
        self.assertIn(
            "config_json::jsonb ->> 'priority'",
            postgres_sql("SELECT json_extract(config_json, '$.priority')"),
        )
        self.assertEqual(
            postgres_sql("SELECT 1 WHERE address LIKE '%부산%' LIMIT ?"),
            "SELECT 1 WHERE address LIKE '%%부산%%' LIMIT %s",
        )

    def test_postgres_schema_has_every_application_table_without_extensions(self) -> None:
        ddl = "\n".join(postgres_schema_statements())
        with tempfile.TemporaryDirectory() as tmp:
            database = Database(Path(tmp) / "schema.db")
            database.initialize()
            with database.session() as conn:
                sqlite_tables = {
                    row["name"]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
        for table in sqlite_tables - {"sqlite_sequence"}:
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", ddl)
        self.assertNotIn("CREATE EXTENSION", ddl)
        self.assertNotIn("AUTOINCREMENT", ddl)
        self.assertNotIn("PRAGMA", ddl)
        self.assertNotRegex(ddl, r"\bREAL\b")
        self.assertIn("DOUBLE PRECISION", ddl)
        signature = expected_schema_signature(postgres_schema_statements())
        self.assertEqual(signature["restaurants"]["id"], "bigint")
        self.assertEqual(signature["restaurants"]["longitude"], "double precision")
        self.assertEqual(
            signature["restaurant_ai_summaries"]["restaurant_id"],
            "bigint",
        )
        self.assertEqual(
            signature["restaurant_ai_summaries"]["summarized_review_count"],
            "integer",
        )
        self.assertEqual(signature["user_saved_restaurants"]["user_id"], "bigint")
        self.assertEqual(signature["user_saved_restaurants"]["restaurant_id"], "bigint")
        self.assertEqual(
            signature["app_schema_migrations"]["applied_at"],
            "timestamp with time zone",
        )

    def test_user_interactions_are_an_explicit_additive_migration(self) -> None:
        ddl = "\n".join(USER_INTERACTIONS_STATEMENTS)
        self.assertIn("CREATE TABLE IF NOT EXISTS review_reactions", ddl)
        self.assertIn("CREATE TABLE IF NOT EXISTS restaurant_user_images", ddl)
        self.assertIn("CREATE INDEX IF NOT EXISTS idx_review_reactions_user", ddl)
        self.assertNotRegex(ddl, r"(?im)^\s*(DROP|TRUNCATE|DELETE|ALTER)\b")
        self.assertIn(
            USER_INTERACTIONS_VERSION,
            {version for version, _description, _statements in REQUIRED_POSTGRES_MIGRATIONS},
        )

    def test_operation_jobs_are_an_explicit_additive_migration(self) -> None:
        ddl = "\n".join(OPERATION_JOBS_STATEMENTS)
        self.assertIn("CREATE TABLE IF NOT EXISTS operation_jobs", ddl)
        self.assertIn("idx_operation_jobs_active_dedupe", ddl)
        self.assertNotRegex(ddl, r"(?im)^\s*(DROP|TRUNCATE|DELETE|ALTER)\b")
        self.assertIn(
            OPERATION_JOBS_VERSION,
            {version for version, _description, _statements in REQUIRED_POSTGRES_MIGRATIONS},
        )

    def test_production_requires_postgres(self) -> None:
        with patch.dict(os.environ, {"APP_ENV": "production", "DATABASE_URL": ""}, clear=True):
            with self.assertRaisesRegex(ValueError, "requires a PostgreSQL"):
                load_settings()

    def test_production_requires_privacy_contact_email(self) -> None:
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "production",
                "DATABASE_URL": "postgresql://restaurant_app@127.0.0.1/example",
                "PRIVACY_CONTACT_EMAIL": "",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "PRIVACY_CONTACT_EMAIL"):
                load_settings()

    def test_postgres_runtime_prepare_never_initializes_schema(self) -> None:
        database = Database("postgresql://restaurant_app@127.0.0.1/example_test")
        with patch.object(database, "verify_schema") as verify_schema, patch.object(
            database, "_initialize_postgres"
        ) as initialize_postgres:
            database.prepare()
        verify_schema.assert_called_once_with()
        initialize_postgres.assert_not_called()

    def test_migration_rejects_default_live_sqlite_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "var" / "public_restaurant.db"
            path.parent.mkdir()
            path.touch()
            with self.assertRaisesRegex(ValueError, "backup copy"):
                require_sqlite_copy(path)

    def test_transfer_result_fails_closed(self) -> None:
        self.assertTrue(transfer_succeeded({"status": "dry_run", "errors": []}))
        self.assertTrue(transfer_succeeded({"status": "applied", "errors": []}))
        self.assertFalse(transfer_succeeded({"status": "schema_mismatch", "errors": []}))
        self.assertFalse(
            transfer_succeeded({"status": "completed_with_errors", "errors": [{"row": 1}]})
        )

    def test_transfer_streams_small_batches_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Database(Path(tmp) / "source-copy.db")
            target = Database(Path(tmp) / "rollback-target.db")
            source.initialize()
            target.initialize()
            with source.session() as conn:
                conn.execute("INSERT INTO users (display_name) VALUES (?)", ("fixture-user",))

            first = transfer(source, target, apply=True, batch_size=3)
            second = transfer(source, target, apply=True, batch_size=2)

            self.assertTrue(transfer_succeeded(first))
            self.assertTrue(transfer_succeeded(second))
            self.assertEqual(first["tables"]["institutions"]["source"], source.count("institutions"))
            self.assertEqual(target.count("users"), 1)
            self.assertEqual(fingerprint(source), fingerprint(target))


if __name__ == "__main__":
    unittest.main()
