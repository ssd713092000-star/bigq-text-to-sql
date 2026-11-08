"""Settings from environment variables (infra/.env locally, Cloud Run env + Secret Manager in prod)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

# Postgres schemas, one database: app tables (Alembic), LangGraph checkpoints, Mem0 vectors.
APP_SCHEMA = "app"
CHECKPOINT_SCHEMA = "checkpoints"
MEM0_SCHEMA = "mem0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    env: str = Field("local", alias="APP_ENV")  # local | prod
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    git_sha: str = Field("dev", alias="GIT_SHA")
    web_origin: str = Field("http://localhost:3000", alias="WEB_ORIGIN")  # CORS: only the web app

    # DB_HOST is a hostname, or a Unix socket directory on Cloud Run: /cloudsql/PROJECT:REGION:INSTANCE
    db_host: str = Field("localhost", alias="DB_HOST")
    db_port: int = Field(5432, alias="DB_PORT")
    db_name: str = Field("analyst", alias="DB_NAME")
    db_user: str = Field("analyst", alias="DB_USER")
    db_password: str = Field("", alias="DB_PASSWORD")
    db_pool_size: int = Field(5, alias="DB_POOL_SIZE")

    mcp_url: str = Field("http://mcp:8000/mcp", alias="MCP_URL")

    @property
    def _is_socket(self) -> bool:
        return self.db_host.startswith("/")

    def sqlalchemy_url(self) -> URL:
        """postgresql+asyncpg URL; asyncpg takes a socket directory as the host query parameter."""
        if self._is_socket:
            return URL.create("postgresql+asyncpg", username=self.db_user, password=self.db_password,
                              database=self.db_name, query={"host": self.db_host})
        return URL.create("postgresql+asyncpg", username=self.db_user, password=self.db_password,
                          host=self.db_host, port=self.db_port, database=self.db_name)

    def psycopg_conninfo(self, search_path: str | None = None) -> str:
        """libpq conninfo for psycopg 3 (LangGraph checkpointer, Mem0). Works for TCP and Unix sockets."""
        from psycopg.conninfo import make_conninfo

        kw: dict[str, str | int] = {"host": self.db_host, "dbname": self.db_name, "user": self.db_user}
        if not self._is_socket:
            kw["port"] = self.db_port
        if self.db_password:
            kw["password"] = self.db_password
        if search_path:
            kw["options"] = f"-c search_path={search_path}"
        return make_conninfo(**kw)


@lru_cache
def get_settings() -> Settings:
    return Settings()
