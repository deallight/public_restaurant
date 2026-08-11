from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterator, Sequence


def normalize_db_value(value: Any) -> Any:
    """Normalize PostgreSQL driver values for the JSON-facing service layer."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def normalize_row(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: normalize_db_value(value) for key, value in dict(row).items()}


def postgres_sql(sql: str) -> str:
    """Translate the application's compact SQL conventions for psycopg."""
    translated = re.sub(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", sql, flags=re.I)
    ignored_insert = translated != sql
    # psycopg uses percent-style binding. Escape SQL LIKE literals before
    # introducing %s placeholders.
    translated = translated.replace("%", "%%").replace("?", "%s")
    translated = re.sub(
        r"date\(\s*'now'\s*,\s*'-6 months'\s*\)",
        "(CURRENT_DATE - INTERVAL '6 months')",
        translated,
        flags=re.I,
    )
    translated = re.sub(
        r"datetime\(\s*'now'\s*,\s*'-1 hour'\s*\)",
        "(CURRENT_TIMESTAMP - INTERVAL '1 hour')",
        translated,
        flags=re.I,
    )
    translated = re.sub(
        r"\bcreated_at\s*>=\s*\(CURRENT_TIMESTAMP - INTERVAL '1 hour'\)",
        "created_at::timestamptz >= (CURRENT_TIMESTAMP - INTERVAL '1 hour')",
        translated,
        flags=re.I,
    )
    translated = re.sub(
        r"\brel\.used_date\s*>=\s*\(CURRENT_DATE - INTERVAL '6 months'\)",
        "rel.used_date::date >= (CURRENT_DATE - INTERVAL '6 months')",
        translated,
        flags=re.I,
    )
    translated = re.sub(
        r"json_extract\(([^,]+),\s*'\$\.([^']+)'\)",
        r"(\1::jsonb ->> '\2')",
        translated,
        flags=re.I,
    )
    if ignored_insert and "ON CONFLICT" not in translated.upper():
        translated = translated.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    return translated


@dataclass
class PostgresCursor:
    _cursor: Any
    lastrowid: int | None = None

    @property
    def rowcount(self) -> int:
        return int(self._cursor.rowcount)

    def fetchone(self) -> dict[str, Any] | None:
        return normalize_row(self._cursor.fetchone())

    def fetchall(self) -> list[dict[str, Any]]:
        return [normalize_row(row) or {} for row in self._cursor.fetchall()]

    def fetchmany(self, size: int = 500) -> list[dict[str, Any]]:
        return [normalize_row(row) or {} for row in self._cursor.fetchmany(size)]

    def __iter__(self) -> Iterator[dict[str, Any]]:
        for row in self._cursor:
            normalized = normalize_row(row)
            if normalized is not None:
                yield normalized


class PostgresConnection:
    backend = "postgresql"
    tables_without_id = {
        "app_schema_migrations",
        "restaurant_ai_summaries",
        "review_reactions",
        "user_saved_restaurants",
    }

    def __init__(self, raw_connection: Any):
        self.raw_connection = raw_connection

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> PostgresCursor:
        translated = postgres_sql(sql)
        insert_match = re.match(
            r"\s*INSERT\s+INTO\s+([a-z_]+)\b",
            translated,
            flags=re.I,
        )
        is_insert = insert_match is not None
        insert_table = insert_match.group(1).lower() if insert_match else ""
        if (
            is_insert
            and "RETURNING" not in translated.upper()
            and "ON CONFLICT" not in translated.upper()
            and insert_table not in self.tables_without_id
        ):
            translated = translated.rstrip().rstrip(";") + " RETURNING id"
        cursor = self.raw_connection.cursor()
        transaction_control = bool(
            re.match(r"\s*(SAVEPOINT|RELEASE|ROLLBACK\s+TO|BEGIN|COMMIT|ROLLBACK)\b", translated, flags=re.I)
        )
        needs_guard = not transaction_control and bool(
            re.match(r"\s*(INSERT|UPDATE|DELETE)\b", translated, flags=re.I)
        )
        if needs_guard:
            guard = self.raw_connection.cursor()
            guard.execute("SAVEPOINT db_compat_statement")
        try:
            cursor.execute(translated, tuple(params or ()))
        except Exception:
            if needs_guard:
                guard.execute("ROLLBACK TO SAVEPOINT db_compat_statement")
                guard.execute("RELEASE SAVEPOINT db_compat_statement")
            raise
        lastrowid = None
        if is_insert and cursor.description:
            row = cursor.fetchone()
            if row is not None:
                lastrowid = int(row["id"])
        if needs_guard:
            guard.execute("RELEASE SAVEPOINT db_compat_statement")
        return PostgresCursor(cursor, lastrowid=lastrowid)

    def executemany(self, sql: str, params: Sequence[Sequence[Any]]) -> PostgresCursor:
        cursor = self.raw_connection.cursor()
        cursor.executemany(postgres_sql(sql), params)
        return PostgresCursor(cursor)

    def commit(self) -> None:
        self.raw_connection.commit()

    def rollback(self) -> None:
        self.raw_connection.rollback()

    def close(self) -> None:
        self.raw_connection.close()


def connect_postgres(database_url: str) -> PostgresConnection:
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:  # pragma: no cover - depends on deployment environment
        raise RuntimeError(
            "PostgreSQL requires psycopg; install requirements.txt in the application environment"
        ) from exc
    return PostgresConnection(psycopg.connect(database_url, row_factory=dict_row))
