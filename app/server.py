from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from .config import load_settings
from .http_server import serve


ROOT_DIR = Path(__file__).resolve().parent.parent


def _start_local_worker(poll_interval: float) -> subprocess.Popen:
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "app.worker",
            "--poll-interval",
            str(max(0.1, float(poll_interval))),
        ],
        cwd=ROOT_DIR,
        start_new_session=True,
    )


def _stop_local_worker(worker_process: subprocess.Popen) -> None:
    if worker_process.poll() is not None:
        return
    worker_process.terminate()
    try:
        worker_process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        worker_process.kill()
        worker_process.wait(timeout=5)


def main() -> None:
    settings = load_settings()
    parser = argparse.ArgumentParser(description="Run the public restaurant map service.")
    parser.add_argument("--host", default=settings.host)
    parser.add_argument("--port", type=int, default=settings.port)
    parser.add_argument(
        "--no-worker",
        action="store_true",
        help="do not start the local operation worker with the development server",
    )
    parser.add_argument("--worker-poll-interval", type=float, default=1.0)
    args = parser.parse_args()

    runtime_settings = replace(
        settings,
        host=args.host,
        port=args.port,
    )
    server = serve(runtime_settings)
    worker_process = None
    try:
        if settings.app_env != "production" and not args.no_worker:
            worker_process = _start_local_worker(args.worker_poll_interval)
            print(f"Local operation worker started (PID {worker_process.pid})")
        print(f"Serving on http://{args.host}:{args.port}")
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if worker_process is not None:
            _stop_local_worker(worker_process)
        server.server_close()


if __name__ == "__main__":
    main()
