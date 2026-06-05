from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import load_settings
from app.database import Database


def main() -> None:
    settings = load_settings()
    Database(settings.db_path).initialize()
    print(f"Initialized {settings.db_path}")


if __name__ == "__main__":
    main()
