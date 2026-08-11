from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents import VerifierAgent
from app.database import APP_TABLES, Database
from app.pipeline import BusanCityLiveAdapter, DailyPipeline


def main() -> None:
    database_url = os.getenv("TEST_DATABASE_URL", "").strip()
    database_name = urlsplit(database_url).path.lstrip("/")
    if not database_url.startswith(("postgresql://", "postgres://")):
        raise SystemExit("TEST_DATABASE_URL must point to PostgreSQL")
    if "test" not in database_name.lower():
        raise SystemExit("TEST_DATABASE_URL database name must contain 'test'")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        database = Database(database_url)
        database.initialize()
        with database.session() as conn:
            conn.execute(
                f"TRUNCATE TABLE {', '.join(APP_TABLES)} RESTART IDENTITY CASCADE"
            )
        database.initialize()
        adapter = BusanCityLiveAdapter(
            max_pages=1,
            max_documents=1,
            raw_dir=root / "raw" / "busan_city",
        )
        result = DailyPipeline(
            database,
            adapter=adapter,
            verifier=VerifierAgent(),
        ).run()
        with database.session() as conn:
            result["documents"] = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT source_title, published_at, raw_content_path, metadata_json
                    FROM raw_documents
                    ORDER BY id DESC
                    LIMIT 3
                    """
                )
            ]
            result["expenses"] = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT department_name, used_date, place_name, amount
                    FROM expense_records
                    ORDER BY id
                    LIMIT 5
                    """
                )
            ]
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
