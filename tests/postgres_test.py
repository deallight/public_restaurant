from __future__ import annotations

import os
from urllib.parse import urlsplit, urlunsplit

from app.config import load_settings
from app.database import APP_TABLES, Database


def postgres_test_url() -> str:
    configured = os.getenv("TEST_DATABASE_URL", "").strip()
    if not configured:
        source = load_settings().database_url
        parts = urlsplit(source)
        database_name = parts.path.lstrip("/")
        if parts.netloc:
            configured = urlunsplit(
                (
                    parts.scheme,
                    parts.netloc,
                    f"/{database_name}_test",
                    parts.query,
                    parts.fragment,
                )
            )
        else:
            configured = f"{parts.scheme}:///{database_name}_test"
            if parts.query:
                configured += f"?{parts.query}"
    parts = urlsplit(configured)
    database_name = parts.path.lstrip("/")
    if not configured.startswith(("postgresql://", "postgres://")):
        raise RuntimeError("TEST_DATABASE_URL must point to PostgreSQL")
    if "test" not in database_name.lower():
        raise RuntimeError("TEST_DATABASE_URL database name must contain 'test'")
    return configured


def reset_postgres_database(database: Database) -> None:
    with database.session() as conn:
        conn.execute(
            f"TRUNCATE TABLE {', '.join(APP_TABLES)} RESTART IDENTITY CASCADE"
        )
    database.initialize()


def fresh_postgres_database() -> Database:
    database = Database(postgres_test_url())
    database.initialize()
    reset_postgres_database(database)
    return database
