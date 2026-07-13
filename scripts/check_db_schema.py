from __future__ import annotations

import json
import os

from app.database import Database


def main() -> None:
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url.startswith(("postgresql://", "postgres://")):
        raise SystemExit("DATABASE_URL must point to PostgreSQL")
    issues = Database(database_url).schema_issues(require_migration=True)
    report = {
        "status": "compatible" if not issues else "schema_mismatch",
        "schema_issues": issues,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if issues:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
