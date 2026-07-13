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
        app_env=settings.app_env,
        host=args.host,
        port=args.port,
        naver_map_key=settings.naver_map_key,
        naver_maps_client_id=settings.naver_maps_client_id,
        naver_maps_client_secret=settings.naver_maps_client_secret,
        naver_search_client_id=settings.naver_search_client_id,
        naver_search_client_secret=settings.naver_search_client_secret,
        naver_login_client_id=settings.naver_login_client_id,
        naver_login_client_secret=settings.naver_login_client_secret,
        naver_login_redirect_uri=settings.naver_login_redirect_uri,
        google_client_id=settings.google_client_id,
        google_client_secret=settings.google_client_secret,
        google_redirect_uri=settings.google_redirect_uri,
        data_go_kr_service_key=settings.data_go_kr_service_key,
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
