from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import MutableMapping


BASE_DIR = Path(__file__).resolve().parent.parent


def read_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def load_dotenv(
    path: Path | None = None,
    environ: MutableMapping[str, str] | None = None,
) -> dict[str, str]:
    loaded = read_dotenv(path or BASE_DIR / ".env")
    target = environ if environ is not None else os.environ
    for key, value in loaded.items():
        target.setdefault(key, value)
    return loaded


@dataclass(frozen=True)
class Settings:
    db_path: Path
    host: str = "127.0.0.1"
    port: int = 8000
    naver_map_key: str = ""
    naver_maps_client_id: str = ""
    naver_maps_client_secret: str = ""
    naver_search_client_id: str = ""
    naver_search_client_secret: str = ""
    naver_login_client_id: str = ""
    naver_login_client_secret: str = ""
    naver_login_redirect_uri: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = ""
    data_go_kr_service_key: str = ""
    app_name: str = "공기밥"
    review_rate_limit_per_hour: int = 3


def load_settings() -> Settings:
    load_dotenv()
    db_path = Path(os.getenv("APP_DB_PATH", BASE_DIR / "var" / "public_restaurant.db"))
    return Settings(
        db_path=db_path,
        host=os.getenv("APP_HOST", "127.0.0.1"),
        port=int(os.getenv("APP_PORT", "8000")),
        naver_map_key=os.getenv("NAVER_MAP_KEY", "") or os.getenv("NAVER_MAPS_CLIENT_ID", ""),
        naver_maps_client_id=os.getenv("NAVER_MAPS_CLIENT_ID", "") or os.getenv("NAVER_MAP_KEY", ""),
        naver_maps_client_secret=os.getenv("NAVER_MAPS_CLIENT_SECRET", ""),
        naver_search_client_id=os.getenv("NAVER_SEARCH_CLIENT_ID", ""),
        naver_search_client_secret=os.getenv("NAVER_SEARCH_CLIENT_SECRET", ""),
        naver_login_client_id=os.getenv("NAVER_LOGIN_CLIENT_ID", ""),
        naver_login_client_secret=os.getenv("NAVER_LOGIN_CLIENT_SECRET", ""),
        naver_login_redirect_uri=os.getenv(
            "NAVER_LOGIN_REDIRECT_URI",
            "http://127.0.0.1:8000/auth/callback/naver",
        ),
        google_client_id=os.getenv("GOOGLE_CLIENT_ID", ""),
        google_client_secret=os.getenv("GOOGLE_CLIENT_SECRET", ""),
        google_redirect_uri=os.getenv(
            "GOOGLE_REDIRECT_URI",
            "http://127.0.0.1:8000/auth/callback/google",
        ),
        data_go_kr_service_key=os.getenv("DATA_GO_KR_SERVICE_KEY", ""),
        review_rate_limit_per_hour=int(os.getenv("REVIEW_RATE_LIMIT_PER_HOUR", "3")),
    )
