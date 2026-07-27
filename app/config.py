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
    database_url: str = ""
    restaurant_image_upload_dir: Path | None = None
    app_env: str = "development"
    host: str = "127.0.0.1"
    port: int = 8000
    naver_map_key: str = ""
    naver_maps_client_id: str = ""
    naver_maps_client_secret: str = ""
    naver_search_client_id: str = ""
    naver_search_client_secret: str = ""
    naver_api_hub_client_id: str = ""
    naver_api_hub_client_secret: str = ""
    naver_login_client_id: str = ""
    naver_login_client_secret: str = ""
    naver_login_redirect_uri: str = ""
    session_secret: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = ""
    data_go_kr_service_key: str = ""
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    naver_search_daily_quota: int = 25_000
    naver_api_hub_monthly_quota: int = 775_000
    data_go_kr_daily_quota: int = 10_000
    groq_daily_quota: int = 1_000
    app_name: str = "공기밥"
    review_rate_limit_per_hour: int = 3


def load_settings() -> Settings:
    load_dotenv()
    db_path = Path(os.getenv("APP_DB_PATH", BASE_DIR / "var" / "public_restaurant.db"))
    database_url = os.getenv("DATABASE_URL", "").strip()
    app_env = os.getenv("APP_ENV", "development").strip().lower()
    if database_url and not database_url.startswith(("postgresql://", "postgres://", "sqlite:///")):
        raise ValueError("DATABASE_URL must use postgresql:// or sqlite:///")
    if app_env == "production" and not database_url.startswith(("postgresql://", "postgres://")):
        raise ValueError("APP_ENV=production requires a PostgreSQL DATABASE_URL")
    return Settings(
        db_path=db_path,
        database_url=database_url,
        restaurant_image_upload_dir=Path(
            os.getenv(
                "RESTAURANT_IMAGE_UPLOAD_DIR",
                BASE_DIR / "var" / "restaurant_images",
            )
        ),
        app_env=app_env,
        host=os.getenv("APP_HOST", "127.0.0.1"),
        port=int(os.getenv("APP_PORT", "8000")),
        naver_map_key=os.getenv("NAVER_MAP_KEY", "") or os.getenv("NAVER_MAPS_CLIENT_ID", ""),
        naver_maps_client_id=os.getenv("NAVER_MAPS_CLIENT_ID", "") or os.getenv("NAVER_MAP_KEY", ""),
        naver_maps_client_secret=os.getenv("NAVER_MAPS_CLIENT_SECRET", ""),
        naver_search_client_id=os.getenv("NAVER_SEARCH_CLIENT_ID", ""),
        naver_search_client_secret=os.getenv("NAVER_SEARCH_CLIENT_SECRET", ""),
        naver_api_hub_client_id=os.getenv("NAVER_API_HUB_CLIENT_ID", ""),
        naver_api_hub_client_secret=os.getenv("NAVER_API_HUB_CLIENT_SECRET", ""),
        naver_login_client_id=os.getenv("NAVER_LOGIN_CLIENT_ID", ""),
        naver_login_client_secret=os.getenv("NAVER_LOGIN_CLIENT_SECRET", ""),
        naver_login_redirect_uri=os.getenv(
            "NAVER_LOGIN_REDIRECT_URI",
            "http://127.0.0.1:8000/auth/callback/naver",
        ),
        session_secret=os.getenv("APP_SESSION_SECRET", ""),
        google_client_id=os.getenv("GOOGLE_CLIENT_ID", ""),
        google_client_secret=os.getenv("GOOGLE_CLIENT_SECRET", ""),
        google_redirect_uri=os.getenv(
            "GOOGLE_REDIRECT_URI",
            "http://127.0.0.1:8000/auth/callback/google",
        ),
        data_go_kr_service_key=os.getenv("DATA_GO_KR_SERVICE_KEY", ""),
        groq_api_key=os.getenv("GROQ_API_KEY", "").strip(),
        groq_model=(
            os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
            or "llama-3.3-70b-versatile"
        ),
        naver_search_daily_quota=max(
            0, int(os.getenv("NAVER_SEARCH_DAILY_QUOTA", "25000"))
        ),
        naver_api_hub_monthly_quota=max(
            0, int(os.getenv("NAVER_API_HUB_MONTHLY_QUOTA", "775000"))
        ),
        data_go_kr_daily_quota=max(
            0, int(os.getenv("DATA_GO_KR_DAILY_QUOTA", "10000"))
        ),
        groq_daily_quota=max(0, int(os.getenv("GROQ_DAILY_QUOTA", "1000"))),
        review_rate_limit_per_hour=int(os.getenv("REVIEW_RATE_LIMIT_PER_HOUR", "3")),
    )
