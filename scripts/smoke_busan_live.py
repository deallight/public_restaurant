from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents import VerifierAgent
from app.database import Database
from app.pipeline import BusanCityLiveAdapter, DailyPipeline


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        database = Database(root / "smoke.db")
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
