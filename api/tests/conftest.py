"""Test fixtures. DB tests use a throwaway database `analyst_test` on the Postgres at DB_HOST:DB_PORT
(the compose container locally, a service container in CI), built by the real migrate code.
Skipped automatically when no Postgres is reachable."""

from __future__ import annotations

import json
import os

import psycopg
import pytest
from cryptography.fernet import Fernet

TEST_DB = "analyst_test"
CLIENT_ID = "test-client.apps.googleusercontent.com"


def _env(name: str, default: str) -> str:
    return os.environ.get(name) or default


def _admin_conninfo(dbname: str | None = None) -> str:
    return psycopg.conninfo.make_conninfo(
        host=_env("DB_HOST", "127.0.0.1"), port=_env("DB_PORT", "5432"),
        dbname=dbname or _env("DB_ADMIN_DB", "analyst"),
        user=_env("DB_USER", "analyst"), password=_env("DB_PASSWORD", "test"), connect_timeout=3,
    )


def _clear_caches() -> None:
    from app import google_oauth, security
    from app.config import get_settings

    get_settings.cache_clear()
    security._fernet.cache_clear()
    google_oauth._cache.clear()


@pytest.fixture(scope="session")
def pg_database():
    try:
        with psycopg.connect(_admin_conninfo(), autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
            conn.execute(f'CREATE DATABASE "{TEST_DB}"')
    except psycopg.OperationalError as e:
        if os.environ.get("REQUIRE_DB_TESTS"):  # CI: a missing DB must fail, not silently skip
            raise
        pytest.skip(f"Postgres not reachable for DB tests ({e.__class__.__name__})")

    mp = pytest.MonkeyPatch()
    for k, v in {"DB_HOST": _env("DB_HOST", "127.0.0.1"), "DB_PORT": _env("DB_PORT", "5432"),
                 "DB_USER": _env("DB_USER", "analyst"), "DB_PASSWORD": _env("DB_PASSWORD", "test"),
                 "DB_NAME": TEST_DB}.items():
        mp.setenv(k, v)
    _clear_caches()
    from app import migrate

    migrate.main()  # extension, schemas, Alembic head, LangGraph checkpointer tables
    yield TEST_DB
    mp.undo()
    _clear_caches()


@pytest.fixture
def api(pg_database, monkeypatch):
    """TestClient on a clean app schema, with Google ID-token verification faked.
    Fake ID tokens are 'fake:' + JSON claims; see make_id_token()."""
    monkeypatch.setenv("DB_NAME", pg_database)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    _clear_caches()

    with psycopg.connect(_admin_conninfo(pg_database), autocommit=True) as c:
        c.execute("TRUNCATE app.sessions, app.user_credentials, app.users, app.orgs RESTART IDENTITY CASCADE")

    from app import google_oauth

    def fake_verify(token: str, client_id: str) -> dict:
        assert client_id == CLIENT_ID
        if not token.startswith("fake:"):
            raise ValueError("Wrong number of segments in token")
        return json.loads(token[5:])

    monkeypatch.setattr(google_oauth, "_verify_sync", fake_verify)

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        yield client
    _clear_caches()


def make_id_token(email: str, *, hd: str | None = None, sub: str | None = None, verified: bool = True,
                  name: str = "Test User") -> str:
    claims = {"iss": "https://accounts.google.com", "aud": CLIENT_ID, "sub": sub or f"sub-{email}",
              "email": email, "email_verified": verified, "name": name, "picture": "https://example.com/p.png"}
    if hd:
        claims["hd"] = hd
    return "fake:" + json.dumps(claims) + " " * 20  # pad past the min length


BQ = "openid email profile https://www.googleapis.com/auth/bigquery"


def sign_in(client, email: str, *, refresh_token: str | None = "1//refresh-" + "x" * 30, scope: str = BQ, **kw):
    return client.post("/auth/session", json={"id_token": make_id_token(email, **kw),
                                              "refresh_token": refresh_token, "scope": scope})
