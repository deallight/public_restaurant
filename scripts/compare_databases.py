from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app.database import Database
from app.services import RestaurantService
from scripts.db_transfer import fingerprint, require_sqlite_copy, schema_diff


def service_contract(database: Database) -> dict:
    service = RestaurantService(database)
    restaurants = service.list_map_restaurants()
    return {
        "map": restaurants,
        "rankings": service.rankings(),
        "search": service.search("부산"),
        "details": [service.get_restaurant(int(item["id"])) for item in restaurants],
        "sources": service.source_registry(),
        "verification": service.verification_overview(),
        "review_queue": service.admin_review_queue(),
        "admin_candidates": service.admin_candidates(limit=100),
        "map_issues": service.map_issue_candidates(),
        "collection_progress": service.collection_progress("2026-01-01", "2026-12-31"),
        "collection_dashboard": service.collection_dashboard("2026-01-01", "2026-12-31"),
        "documents": service.admin_documents(
            start_date="2026-01-01", end_date="2026-12-31", limit=100
        ),
        "ops_logs": service.ops_logs(limit=100),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare SQLite and PostgreSQL counts and API contracts.")
    parser.add_argument("--sqlite-copy", required=True, type=Path)
    args = parser.parse_args()
    require_sqlite_copy(args.sqlite_copy)
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url.startswith(("postgresql://", "postgres://")):
        raise SystemExit("DATABASE_URL must point to PostgreSQL")
    sqlite_db = Database(args.sqlite_copy)
    postgres_db = Database(database_url)
    schema_issues = postgres_db.schema_issues(require_migration=True)
    if schema_issues:
        print(
            json.dumps(
                {"status": "schema_mismatch", "schema_issues": schema_issues},
                ensure_ascii=False,
                indent=2,
            )
        )
        raise SystemExit(1)
    sqlite_fingerprint = fingerprint(sqlite_db)
    postgres_fingerprint = fingerprint(postgres_db)
    sqlite_contract = service_contract(sqlite_db)
    postgres_contract = service_contract(postgres_db)
    report = {
        "schema_diff": schema_diff(sqlite_db, postgres_db),
        "fingerprints_match": sqlite_fingerprint == postgres_fingerprint,
        "service_contracts_match": sqlite_contract == postgres_contract,
        "sqlite": sqlite_fingerprint,
        "postgresql": postgres_fingerprint,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["schema_diff"] or not report["fingerprints_match"] or not report["service_contracts_match"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
