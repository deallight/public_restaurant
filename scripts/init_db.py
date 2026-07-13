from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import load_settings
from app.database import Database


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize the configured database schema.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="required before applying PostgreSQL DDL; SQLite remains automatic",
    )
    args = parser.parse_args()
    settings = load_settings()
    database = Database(settings.database_url or settings.db_path)
    if database.backend == "postgresql" and not args.apply:
        raise SystemExit("PostgreSQL DDL not applied: review the migration and rerun with --apply")
    database.initialize()
    print(f"Initialized {database.backend} database")


if __name__ == "__main__":
    main()
