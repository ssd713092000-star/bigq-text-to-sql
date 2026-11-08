"""Smoke tests: server imports in AUTH_MODE=none, exposes the expected tools and a health route."""

import importlib
import sys
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("AUTH_MODE", "none")
    mp.setenv("BQ_PROJECT", "test-project")
    mp.syspath_prepend(str(SERVER_DIR))
    sys.modules.pop("bq_mcp_server", None)
    yield importlib.import_module("bq_mcp_server")
    mp.undo()


async def test_tools_registered(server):
    names = {t.name for t in await server.mcp.list_tools()}
    assert {"whoami", "list_tables", "describe_table", "sample_rows", "column_values",
            "validate_sql", "run_sql", "search_examples", "save_example"} <= names


async def test_healthz(server):
    from starlette.testclient import TestClient

    with TestClient(server.mcp.http_app()) as client:
        r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["status"] == "ok"
