from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import load_settings
from app.database import Database
from app.pipeline import DailyPipeline
from app.services import RestaurantService


def main() -> None:
    settings = load_settings()
    parser = argparse.ArgumentParser(description="Re-verify pending restaurant candidates.")
    parser.add_argument("--db", default=str(settings.db_path))
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()

    database = Database(Path(args.db))
    database.initialize()
    service = RestaurantService(database)
    before = service.verification_overview()
    result = DailyPipeline(database, settings=settings).verify_pending(limit=args.limit)
    after = service.verification_overview()
    print(
        json.dumps(
            {
                "integrations": {
                    "naver_search": bool(settings.naver_search_client_id and settings.naver_search_client_secret),
                    "naver_maps_geocoding": bool(
                        settings.naver_maps_client_id and settings.naver_maps_client_secret
                    ),
                    "data_go_kr_permit": bool(settings.data_go_kr_service_key),
                },
                "before": before["counts"],
                "result": result,
                "after": after["counts"],
                "api_summary": after["api_summary"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
