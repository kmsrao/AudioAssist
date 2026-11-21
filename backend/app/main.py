"""Live Audio Assist - FastAPI application entry point.

Run:  python -m app.main [--host 127.0.0.1] [--port 8765] [--no-browser]
"""
from __future__ import annotations

import argparse
import sys
import threading
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from app.api.routes import router as api_router
from app.audio.devices import DeviceManager
from app.config.settings import APP_NAME, APP_VERSION, ServerConfig
from app.database.db import Database
from app.database.repository import Repository
from app.logging.setup import configure_logging, get_logger
from app.services.assist_service import AssistService
from app.utils.platform_tuning import tune_process_for_realtime
from app.websocket.hub import router as ws_router

log = get_logger("main")

_FALLBACK_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>Live Audio Assist</title>
<style>body{background:#0e1116;color:#d8dee9;font-family:system-ui;padding:40px;max-width:760px}
code{background:#1b2230;padding:2px 6px;border-radius:4px}a{color:#7fb4ff}</style></head><body>
<h1>Live Audio Assist backend is running</h1>
<p>The compiled dashboard was not found at <code>{dist}</code>.</p>
<p>Build it with <code>./setup.sh</code> (macOS) or <code>.\\setup.ps1</code> (Windows), or run the Angular dev
server (<code>cd frontend &amp;&amp; npm start</code>) and open <a href="http://localhost:4200">localhost:4200</a>.</p>
<p>API docs: <a href="/docs">/docs</a></p></body></html>"""


def create_app(config: ServerConfig | None = None, service: AssistService | None = None,
               start_service: bool = True) -> FastAPI:
    config = config or ServerConfig.from_env()
    configure_logging(config.logs_dir, config.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        svc = app.state.service
        if start_service:
            tune_process_for_realtime()
            svc.start()
        log.info("application started", extra={"host": config.host, "port": config.port})
        try:
            yield
        finally:
            svc.shutdown()
            log.info("application shutdown complete")

    app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan,
                  description="Real-time analysis and operator recommendations for live sound. "
                              "Never modifies audio or mixer settings.")
    if service is None:
        db = Database(config.db_path)
        service = AssistService(config, Repository(db), DeviceManager())
    app.state.service = service
    app.state.config = config

    # Angular dev server (ng serve on :4200) during development only.
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:4200", "http://127.0.0.1:4200"],
                       allow_methods=["*"], allow_headers=["*"])

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):  # noqa: ANN202
        log.exception("unhandled API error", extra={"path": request.url.path})
        return JSONResponse(status_code=500, content={"detail": f"Internal error: {exc}"})

    app.include_router(api_router)
    app.include_router(ws_router)

    dist: Path = config.frontend_dist

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):  # noqa: ANN202
        if full_path.startswith(("api/", "ws")):
            return JSONResponse(status_code=404, content={"detail": "Not found"})
        index = dist / "index.html"
        if not index.exists():
            return HTMLResponse(_FALLBACK_HTML.replace("{dist}", str(dist)))
        candidate = (dist / full_path).resolve()
        if full_path and candidate.is_file() and dist.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index)

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--host", help="bind address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, help="port (default 8765)")
    parser.add_argument("--no-browser", action="store_true", help="do not open the dashboard in a browser")
    parser.add_argument("--log-level", help="DEBUG, INFO, WARNING ...")
    args = parser.parse_args(argv)

    config = ServerConfig.from_env()
    if args.host:
        config.host = args.host
    if args.port:
        config.port = args.port
    if args.no_browser:
        config.open_browser = False
    if args.log_level:
        config.log_level = args.log_level.upper()

    import uvicorn

    app = create_app(config)
    url = f"http://{'localhost' if config.host in ('127.0.0.1', '0.0.0.0') else config.host}:{config.port}"
    if config.open_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"\n  {APP_NAME} {APP_VERSION}\n  Dashboard: {url}\n  API docs:  {url}/docs\n  Press Ctrl+C to stop.\n",
          flush=True)
    try:
        uvicorn.run(app, host=config.host, port=config.port, log_config=None)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
