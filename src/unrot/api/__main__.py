"""`python -m unrot.api` -- run the backend.

Serves the built frontend too when `ui/dist` exists, so a built checkout is one
command rather than two. In development you run this and `npm run dev` side by
side, and Vite proxies /api here.
"""

from __future__ import annotations

import argparse
import sys

from ..env import load_env
from .app import UI_DIST


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="unrot.api", description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="default 127.0.0.1")
    parser.add_argument("--port", type=int, default=8000, help="default 8000")
    parser.add_argument("--reload", action="store_true", help="reload on code changes")
    args = parser.parse_args(argv)

    # Before uvicorn starts, so the grader sees the key. Without this the
    # server falls back to keyword grading for every answer and reports it as
    # ungraded-by-model -- true, but for a reason nobody would guess.
    loaded = load_env()
    if loaded:
        print(f"config: {loaded[0]}")

    try:
        import uvicorn
    except ImportError:
        print(
            "uvicorn is not installed. Run: uv sync", file=sys.stderr
        )
        return 2

    if not UI_DIST.is_dir():
        print(
            f"No built frontend at {UI_DIST} -- serving the API only."
            "\nFor the UI: cd ui && npm install && npm run dev  (then open :5173)"
        )

    uvicorn.run(
        "unrot.api.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
