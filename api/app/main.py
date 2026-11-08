"""FastAPI entrypoint. Run: uvicorn app.main:app --host 0.0.0.0 --port 8080"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app import db
from app.config import APP_SCHEMA, get_settings
from app.logging_setup import setup_logging
from app.routers import auth as auth_router

log = logging.getLogger("api")


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level)
    db.init_engine(settings)
    log.info("api starting", extra={"env": settings.env, "git_sha": settings.git_sha,
                                    "db_host": settings.db_host, "db_name": settings.db_name})
    yield
    await db.dispose_engine()


app = FastAPI(title="Text-to-SQL Analyst API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().web_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router.router)


@app.get("/healthz", tags=["ops"])
async def healthz() -> dict:
    """Liveness: the process is up. No dependencies, so a DB blip doesn't restart the container."""
    return {"status": "ok"}


@app.get("/readyz", tags=["ops"])
async def readyz() -> JSONResponse:
    """Readiness: config valid, DB reachable and migrated. Cloud Run's startup probe uses this, so a revision
    with a broken key never receives traffic."""
    from app.security import TokenKeyError, _fernet

    try:
        _fernet()
    except TokenKeyError as e:
        log.error("readiness: bad config", extra={"error": str(e)})
        return JSONResponse({"status": "unavailable", "config": "TOKEN_ENCRYPTION_KEY missing or invalid"},
                            status_code=503)
    try:
        async with db.engine().connect() as conn:
            version = (await conn.execute(text(f"SELECT version_num FROM {APP_SCHEMA}.alembic_version"))).scalar()
        return JSONResponse({"status": "ok", "db": "ok", "migration": version})
    except Exception as e:  # noqa: BLE001 - report, don't crash
        log.warning("readiness check failed", extra={"error": f"{type(e).__name__}: {e}"})
        return JSONResponse({"status": "unavailable", "db": "error", "error": type(e).__name__}, status_code=503)


@app.get("/version", tags=["ops"])
async def version() -> dict:
    s = get_settings()
    return {"git_sha": s.git_sha, "env": s.env}
