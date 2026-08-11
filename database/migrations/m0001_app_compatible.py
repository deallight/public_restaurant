from __future__ import annotations

import re


VERSION = "0001"
DESCRIPTION = "application-compatible baseline"

MIGRATION_TABLE_STATEMENT = """
CREATE TABLE IF NOT EXISTS app_schema_migrations (
  version TEXT PRIMARY KEY,
  description TEXT NOT NULL,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
)
""".strip()

POSTGRES_INFORMATION_SCHEMA_TYPES = {
    "BIGSERIAL": "bigint",
    "BIGINT": "bigint",
    "INTEGER": "integer",
    "DOUBLE PRECISION": "double precision",
    "TEXT": "text",
    "TIMESTAMPTZ": "timestamp with time zone",
}


def statements(schema_template: str, dependency_order: list[str]) -> list[str]:
    """Order the authoritative PostgreSQL schema for dependency-safe creation."""
    raw_statements = [part.strip() for part in schema_template.split(";") if part.strip()]
    table_statements: dict[str, str] = {}
    other_statements: list[str] = []
    for statement in raw_statements:
        match = re.match(r"CREATE TABLE IF NOT EXISTS\s+([a-z_]+)", statement, flags=re.I)
        if match:
            table_statements[match.group(1)] = statement
        else:
            other_statements.append(statement)
    ddl = [
        table_statements[name]
        for name in reversed(dependency_order)
        if name in table_statements
    ]
    ddl.extend(other_statements)
    ddl.append(MIGRATION_TABLE_STATEMENT)
    return ddl


def expected_schema_signature(ddl: list[str]) -> dict[str, dict[str, str]]:
    """Return required table/column types as information_schema reports them."""
    signature: dict[str, dict[str, str]] = {}
    for statement in ddl:
        table_match = re.match(
            r"CREATE TABLE IF NOT EXISTS\s+([a-z_]+)\s*\((.*)\)\s*$",
            statement,
            flags=re.I | re.S,
        )
        if not table_match:
            continue
        columns: dict[str, str] = {}
        for raw_line in table_match.group(2).splitlines():
            line = raw_line.strip().rstrip(",")
            column_match = re.match(r"([a-z_]+)\s+(.+)$", line, flags=re.I)
            if not column_match:
                continue
            column_name, declaration = column_match.groups()
            if column_name.upper() in {"UNIQUE", "CHECK", "CONSTRAINT", "PRIMARY", "FOREIGN"}:
                continue
            upper_declaration = declaration.upper()
            for declared_type, information_schema_type in POSTGRES_INFORMATION_SCHEMA_TYPES.items():
                if upper_declaration.startswith(declared_type):
                    columns[column_name] = information_schema_type
                    break
        signature[table_match.group(1)] = columns
    return signature
