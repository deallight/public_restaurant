from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import load_settings
from app.database import Database
from app.pipeline import DailyPipeline


def main() -> None:
    settings = load_settings()
    database = Database(settings.database_url or settings.db_path)
    result = DailyPipeline(database, settings=settings).run()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
