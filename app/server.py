from __future__ import annotations

import argparse
from dataclasses import replace
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

    runtime_settings = replace(
        settings,
        db_path=Path(args.db),
        host=args.host,
        port=args.port,
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
