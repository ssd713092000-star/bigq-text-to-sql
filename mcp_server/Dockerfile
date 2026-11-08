# syntax=docker/dockerfile:1.7
# Build context: repo root.  docker build -f mcp_server/Dockerfile .
FROM ghcr.io/astral-sh/uv:0.12.11 AS uv

FROM python:3.13-slim AS build
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY api/pyproject.toml api/
COPY mcp_server/pyproject.toml mcp_server/
COPY evals/pyproject.toml evals/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-default-groups --package analyst-mcp

FROM python:3.13-slim AS runtime
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin app
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY mcp_server/bq_mcp_server.py mcp_server/rag_store.py /app/
COPY mcp_server/verified_queries /app/verified_queries
ARG GIT_SHA=dev
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 GIT_SHA=$GIT_SHA \
    MCP_HOST=0.0.0.0 PORT=8000
USER app
EXPOSE 8000
CMD ["python", "bq_mcp_server.py"]
