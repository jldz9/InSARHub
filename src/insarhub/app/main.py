"""
Entry point for the insarhub-app command.
Serves the FastAPI backend + built React frontend from a single process.
"""
from __future__ import annotations

import importlib.resources
from pathlib import Path


def serve(host: str = "127.0.0.1", port: int = 8000, reload: bool = False, workdir: str | None = None) -> None:
    import uvicorn
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse
    from .api import app
    from insarhub.app import state
    _settings = state._settings

    if workdir is not None:
        resolved = Path(workdir).expanduser().resolve()
        resolved.mkdir(parents=True, exist_ok=True)
        _settings["workdir"] = str(resolved)

    # Locate the built frontend dist directory bundled with the package
    try:
        # Python 3.9+
        dist_dir = importlib.resources.files("insarhub.app") / "frontend" / "dist"
        dist_path = Path(str(dist_dir))
    except Exception:
        dist_path = Path(__file__).parent / "frontend" / "dist"

    if dist_path.is_dir():
        # Serve static assets (JS/CSS) under /assets
        app.mount("/assets", StaticFiles(directory=str(dist_path / "assets")), name="assets")

        # Catch-all: serve index.html for any non-API route (SPA routing)
        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str):
            index = dist_path / "index.html"
            return FileResponse(str(index))

    import sys
    if sys.platform == "win32":
        import asyncio
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    resolved_workdir = _settings["workdir"]
    print(f"\n  InSARHub is running at http://{host}:{port}\n  Workdir: {resolved_workdir}\n  Open that URL in your browser to get started.\n")
    uvicorn.run(
        app,
        host=host,
        port=port,
    )


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="InSARHub web application")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload (dev only)")
    parser.add_argument("-w", "--workdir", default=None, help="Working directory (default: current directory)")
    parser.add_argument("-v", "--version", action="store_true", help="Print version and exit")
    args = parser.parse_args()
    if args.version:
        from insarhub._version import __version__
        print(__version__)
        return
    serve(host=args.host, port=args.port, reload=args.reload, workdir=args.workdir)


if __name__ == "__main__":
    main()
