"""FastAPI application: JSON API under /api plus the compiled frontend.

Run with: uvicorn --factory app.main:create_app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import logging
import re
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response

from . import cleanup, storage
from .config import Settings, load_settings
from .db import connect, init_db
from .routes import admin_routes, auth_routes, jobs_routes
from .security import cross_site_request, security_headers

log = logging.getLogger("api")
MAX_JSON_BODY = 64 * 1024
UPLOAD_PATH = re.compile(r"^/api/jobs/[0-9a-f]{32}/files$")
API_CLEANUP_INTERVAL = 600


def _cleanup_loop(settings: Settings, stop: threading.Event) -> None:
    # Backup for the worker's cleanup: expired files still get removed if the
    # worker container is down.
    while not stop.wait(API_CLEANUP_INTERVAL):
        try:
            conn = connect(settings.db_path)
            try:
                cleanup.run_cleanup(conn, settings, actor="api")
            finally:
                conn.close()
        except Exception:
            log.exception("api cleanup failed")


def create_app(settings: Settings | None = None, background_cleanup: bool = True) -> FastAPI:
    settings = settings or load_settings(require_secret=True)
    init_db(settings.db_path)
    storage.ensure_layout(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        stop = threading.Event()
        t = None
        if background_cleanup:
            t = threading.Thread(target=_cleanup_loop, args=(settings, stop), daemon=True, name="cleanup")
            t.start()
        yield
        stop.set()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.settings = settings

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/"):
            if cross_site_request(request, settings):
                resp: Response = JSONResponse({"error": "cross_site_rejected"}, status_code=403)
            elif request.method in ("POST", "PUT", "PATCH", "DELETE") and not UPLOAD_PATH.match(path) and (
                    int(request.headers.get("content-length") or 0) > MAX_JSON_BODY
                    or "chunked" in request.headers.get("transfer-encoding", "").lower()):
                resp = JSONResponse({"error": "body_too_large"}, status_code=413)
            else:
                resp = await call_next(request)
        else:
            resp = await call_next(request)
        for k, v in security_headers(settings, path).items():
            resp.headers.setdefault(k, v)
        return resp

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException):
        code = exc.detail if isinstance(exc.detail, str) else "error"
        return JSONResponse({"error": code}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, _exc: RequestValidationError):
        return JSONResponse({"error": "invalid_request"}, status_code=422)

    app.include_router(auth_routes.router)
    app.include_router(jobs_routes.router)
    app.include_router(admin_routes.router)

    static_root = settings.static_dir.resolve()
    index = static_root / "index.html"

    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def spa(full_path: str):
        if full_path.startswith("api/") or full_path == "api":
            return JSONResponse({"error": "not_found"}, status_code=404)
        if full_path:
            candidate = (static_root / full_path).resolve()
            if candidate.is_file() and static_root in candidate.parents:
                cache = "public, max-age=31536000, immutable" if full_path.startswith("assets/") else "no-cache"
                return FileResponse(candidate, headers={"Cache-Control": cache})
        if index.is_file():
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return JSONResponse({"error": "frontend_not_built"}, status_code=404)

    return app


def _configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


_configure_logging()
