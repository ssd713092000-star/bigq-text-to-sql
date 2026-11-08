# syntax=docker/dockerfile:1.7
# Build context: repo root (needs the workspace lock).  docker build -f api/Dockerfile .
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
    uv sync --frozen --no-dev --no-default-groups --package analyst-api

FROM python:3.13-slim AS runtime
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin app
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY api/ /app/
ARG GIT_SHA=dev
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 GIT_SHA=$GIT_SHA PORT=8080
USER app
EXPOSE 8080
# Cloud Run sets PORT; the migrate job overrides the command with: python -m app.migrate
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
