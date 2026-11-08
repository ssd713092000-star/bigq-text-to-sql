"""Database setup, idempotent. Run by the `migrate` compose service and the Cloud Run `migrate` job.

    python -m app.migrate

1. extension `vector` + schemas app / checkpoints / mem0
2. Alembic upgrade head (app tables)
3. LangGraph AsyncPostgresSaver.setup() in schema `checkpoints` (LangGraph owns and versions those tables)
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config

from app.config import APP_SCHEMA, CHECKPOINT_SCHEMA, MEM0_SCHEMA, get_settings
from app.logging_setup import setup_logging

log = logging.getLogger("migrate")
API_DIR = Path(__file__).resolve().parent.parent


async def bootstrap_schemas() -> None:
    s = get_settings()
    async with await psycopg.AsyncConnection.connect(s.psycopg_conninfo(), autocommit=True) as conn:
        # On Cloud SQL, setup_cloudsql.sh already ran this as `postgres`; IF NOT EXISTS makes it a no-op here.
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        for schema in (APP_SCHEMA, CHECKPOINT_SCHEMA, MEM0_SCHEMA):
            await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    log.info("schemas ready", extra={"schemas": [APP_SCHEMA, CHECKPOINT_SCHEMA, MEM0_SCHEMA]})


def alembic_upgrade() -> None:
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "migrations"))
    cfg.attributes["skip_logging"] = True  # keep our JSON logging instead of alembic.ini's
    command.upgrade(cfg, "head")
    log.info("alembic upgraded to head")


async def setup_checkpointer() -> None:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    conninfo = get_settings().psycopg_conninfo(search_path=CHECKPOINT_SCHEMA)
    # from_conn_string opens an autocommit connection, which setup() needs (CREATE INDEX CONCURRENTLY)
    async with AsyncPostgresSaver.from_conn_string(conninfo) as saver:
        await saver.setup()
    log.info("langgraph checkpointer tables ready", extra={"schema": CHECKPOINT_SCHEMA})


def main() -> None:
    setup_logging(get_settings().log_level)
    loop = asyncio.SelectorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()  # psycopg on Windows
    try:
        loop.run_until_complete(bootstrap_schemas())
        alembic_upgrade()
        loop.run_until_complete(setup_checkpointer())
    finally:
        loop.close()
    log.info("migration complete")


if __name__ == "__main__":
    main()
