from __future__ import annotations

import argparse
from pathlib import Path

from .config import load_settings
from .http_server import serve


def main() -> None:
    settings = load_settings()
    parser = argparse.ArgumentParser(description="Run the public restaurant map service.")
    parser.add_argument("--host", default=settings.host)
    parser.add_argument("--port", type=int, default=settings.port)
    parser.add_argument("--db", default=str(settings.db_path))
    args = parser.parse_args()

    runtime_settings = settings.__class__(
        db_path=Path(args.db),
        database_url=settings.database_url,
        restaurant_image_upload_dir=settings.restaurant_image_upload_dir,
        app_env=settings.app_env,
        host=args.host,
        port=args.port,
        naver_map_key=settings.naver_map_key,
        naver_maps_client_id=settings.naver_maps_client_id,
        naver_maps_client_secret=settings.naver_maps_client_secret,
        naver_search_client_id=settings.naver_search_client_id,
        naver_search_client_secret=settings.naver_search_client_secret,
        naver_api_hub_client_id=settings.naver_api_hub_client_id,
        naver_api_hub_client_secret=settings.naver_api_hub_client_secret,
        naver_login_client_id=settings.naver_login_client_id,
        naver_login_client_secret=settings.naver_login_client_secret,
        naver_login_redirect_uri=settings.naver_login_redirect_uri,
        session_secret=settings.session_secret,
        google_client_id=settings.google_client_id,
        google_client_secret=settings.google_client_secret,
        google_redirect_uri=settings.google_redirect_uri,
        data_go_kr_service_key=settings.data_go_kr_service_key,
        groq_api_key=settings.groq_api_key,
        groq_model=settings.groq_model,
        naver_search_daily_quota=settings.naver_search_daily_quota,
        naver_api_hub_monthly_quota=settings.naver_api_hub_monthly_quota,
        data_go_kr_daily_quota=settings.data_go_kr_daily_quota,
        groq_daily_quota=settings.groq_daily_quota,
        app_name=settings.app_name,
        review_rate_limit_per_hour=settings.review_rate_limit_per_hour,
    )
    server = serve(runtime_settings)
    print(f"Serving on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
