"""FastAPI application factory. Serves the compiled frontend in production."""

from __future__ import annotations

import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, db, jobs
from .api import router
from .security import LocalOnlyMiddleware
from .workspaces import WORKSPACES, checkpoint, get_workspace, members_engine

FRONTEND_DIST = config.REPO_ROOT / "frontend" / "dist"


def init_storage() -> None:
    os.umask(0o077)  # everything we create (SQLite, WAL, uploads) is user-only where the OS supports modes
    d = config.data_dir()
    config.assert_outside_repo(d)
    tmp = d / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(tmp)  # multipart uploads > 1 MB are spooled here, not in the shared OS temp dir
    for path in (d, tmp):
        _private(path)
    for wid in WORKSPACES:
        ws = get_workspace(wid)
        db.migrate(ws)
        checkpoint(ws)  # a member list dropped by migration 0007 does not linger in the WAL
        for path in (ws.root, ws.uploads, ws.references, ws.exports, *ws.root.glob("notes.sqlite3*")):
            _private(path)
    members_engine()  # the shared member list (both workspaces), created if missing
    for path in d.glob("members.sqlite3*"):
        _private(path)


def recover_jobs() -> None:
    """Only the process that runs the job worker (the served app) recovers jobs; `migrate` never does."""
    for wid in WORKSPACES:
        jobs.recover_interrupted(get_workspace(wid))


def _private(path: Path) -> None:
    try:
        path.chmod(0o700 if path.is_dir() else 0o600)
    except OSError:
        pass


@asynccontextmanager
async def _lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    init_storage()
    recover_jobs()
    yield


def create_app(frontend_dist: Path | None = FRONTEND_DIST) -> FastAPI:
    app = FastAPI(
        title="Agenda First Notes",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=_lifespan,
    )
    app.add_middleware(LocalOnlyMiddleware, token=config.api_token())
    app.include_router(router)

    from . import services
    from .llm.base import ProviderError

    @app.exception_handler(services.NotFound)
    async def _not_found(_req, exc):  # type: ignore[no-untyped-def]
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(services.ServiceError)
    async def _bad_request(_req, exc):  # type: ignore[no-untyped-def]
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(ProviderError)
    async def _provider(_req, exc):  # type: ignore[no-untyped-def]
        return JSONResponse({"detail": str(exc)}, status_code=502)

    @app.exception_handler(Exception)
    async def _unhandled(_req, exc):  # type: ignore[no-untyped-def]
        # Never echo request bodies (transcripts) back; keep messages short.
        return JSONResponse({"detail": f"{type(exc).__name__}: {str(exc)[:300]}"}, status_code=500)

    if frontend_dist and (frontend_dist / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str) -> FileResponse:
            candidate = (frontend_dist / path).resolve()
            if path and candidate.is_file() and frontend_dist.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(frontend_dist / "index.html")

    return app
