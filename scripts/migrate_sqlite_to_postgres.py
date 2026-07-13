from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app.database import Database
from scripts.db_transfer import require_sqlite_copy, transfer, transfer_succeeded


def main() -> None:
    parser = argparse.ArgumentParser(description="Idempotently migrate a SQLite backup copy to PostgreSQL.")
    parser.add_argument("--source-copy", required=True, type=Path)
    parser.add_argument("--apply", action="store_true", help="write rows; the default is a dry-run")
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()
    require_sqlite_copy(args.source_copy)
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url.startswith(("postgresql://", "postgres://")):
        raise SystemExit("DATABASE_URL must point to PostgreSQL")
    source = Database(args.source_copy)
    target = Database(database_url)
    schema_issues = target.schema_issues(require_migration=True)
    if schema_issues:
        print(
            json.dumps(
                {"status": "schema_mismatch", "schema_issues": schema_issues},
                ensure_ascii=False,
                indent=2,
            )
        )
        raise SystemExit(1)
    result = transfer(source, target, apply=args.apply, batch_size=args.batch_size)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not transfer_succeeded(result):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
