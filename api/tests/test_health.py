from fastapi.testclient import TestClient

from app.config import Settings


def test_healthz_needs_no_database(monkeypatch):
    # point at a DB that doesn't exist: liveness must still pass, readiness must fail cleanly
    monkeypatch.setenv("DB_HOST", "127.0.0.1")
    monkeypatch.setenv("DB_PORT", "1")
    from app.config import get_settings

    get_settings.cache_clear()
    from app.main import app

    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        r = client.get("/readyz")
        assert r.status_code == 503
        assert r.json()["db"] == "error"


def test_cloud_sql_socket_urls():
    s = Settings(DB_HOST="/cloudsql/proj:asia-south1:analyst", DB_USER="u", DB_PASSWORD="p@ss/word", DB_NAME="analyst")
    url = s.sqlalchemy_url()
    assert url.host is None and url.query["host"] == "/cloudsql/proj:asia-south1:analyst"
    assert url.password == "p@ss/word"  # special characters survive (no hand-built URL strings)
    conninfo = s.psycopg_conninfo(search_path="checkpoints")
    assert "host=/cloudsql/proj:asia-south1:analyst" in conninfo
    assert "port" not in conninfo
    assert "search_path=checkpoints" in conninfo


def test_tcp_urls():
    s = Settings(DB_HOST="postgres", DB_PORT=5433, DB_USER="u", DB_PASSWORD="", DB_NAME="analyst")
    assert s.sqlalchemy_url().port == 5433
    assert "password" not in s.psycopg_conninfo()
