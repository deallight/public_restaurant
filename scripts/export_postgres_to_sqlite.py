from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app.database import Database
from scripts.db_transfer import transfer, transfer_succeeded


def main() -> None:
    parser = argparse.ArgumentParser(description="Export PostgreSQL into a new SQLite rollback database.")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--apply", action="store_true", help="write rows; the default is a dry-run")
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("output already exists; choose a new rollback path")
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url.startswith(("postgresql://", "postgres://")):
        raise SystemExit("DATABASE_URL must point to PostgreSQL")
    source = Database(database_url)
    schema_issues = source.schema_issues(require_migration=True)
    if schema_issues:
        print(
            json.dumps(
                {"status": "schema_mismatch", "schema_issues": schema_issues},
                ensure_ascii=False,
                indent=2,
            )
        )
        raise SystemExit(1)
    target = Database(args.output)
    target.initialize()
    result = transfer(source, target, apply=args.apply, batch_size=args.batch_size)
    if not args.apply and args.output.exists():
        args.output.unlink()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not transfer_succeeded(result):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
