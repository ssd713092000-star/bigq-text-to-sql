# Local development

Everything runs with Docker Compose. Python dependencies are managed with **uv** (one workspace lock, `uv.lock`, at the repo root).

## Prerequisites

- Docker Desktop (Windows/macOS) or Docker Engine + Compose v2 (Linux)
- [uv](https://docs.astral.sh/uv/) and Node 24 (only to run things outside Docker)
- gcloud CLI, signed in for Application Default Credentials:

  ```powershell
  gcloud auth application-default login
  gcloud auth application-default set-quota-project <your-project>
  ```

## First run

```powershell
Copy-Item infra\.env.example infra\.env   # then fill it in
.\scripts\dev.ps1 up                       # Linux/macOS: make up
```

To sign in locally, add `http://localhost:3000/api/auth/callback/google` as an authorized redirect URI (and
`http://localhost:3000` as a JavaScript origin) on your OAuth client. See [auth.md](auth.md).

Tests: `uv run pytest -q`. The api DB tests create a throwaway `analyst_test` database on the compose Postgres, so
export `DB_PASSWORD` from `infra/.env` first. Without a reachable Postgres they're skipped locally; in CI they must run.

| URL | Service |
|---|---|
| http://localhost:3000 | web (Next.js) |
| http://localhost:8080/docs | api (FastAPI, OpenAPI UI) |
| http://localhost:8080/readyz | api readiness (DB + migration version) |
| `127.0.0.1:5432` | Postgres (pgvector), user/password from `infra/.env` |
| internal only | mcp (`http://mcp:8000/mcp` inside the compose network) |

Startup order: `postgres` (healthy) → `migrate` (runs once and exits) → `mcp` (healthy) → `api` (ready) → `web`.

## Server-side Google credentials (ADC mount)

`api` and `mcp` mount your host gcloud folder **read-only** at `/gcloud` and use
`GOOGLE_APPLICATION_CREDENTIALS=/gcloud/application_default_credentials.json`. Set `GCLOUD_CONFIG_DIR` in `infra/.env`:

| Host | `GCLOUD_CONFIG_DIR` |
|---|---|
| Windows | `C:/Users/<you>/AppData/Roaming/gcloud` (that's `%APPDATA%\gcloud`, written with forward slashes) |
| Linux | `/home/<you>/.config/gcloud` |
| macOS | `/Users/<you>/.config/gcloud` |

On **Linux**, the containers run as uid 10001, and gcloud writes the ADC file as `0600`, so the container can't read it. Either run
`chmod 644 ~/.config/gcloud/application_default_credentials.json` (it's your machine, but remember the file is a credential), or
add `user: "${UID}:${GID}"` to `api`/`mcp` in a `docker-compose.override.yaml`. Docker Desktop on Windows/macOS doesn't need either.

No service-account key files are used or committed. On Cloud Run the runtime service account replaces the mount.

## Database

One Postgres database, three schemas:

| Schema | Owner | Contents |
|---|---|---|
| `app` | Alembic (`api/migrations`) | users, orgs, credentials, datasets, examples, chats, query log |
| `checkpoints` | LangGraph `AsyncPostgresSaver.setup()` | `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations` |
| `mem0` | Mem0 (phase 7) | pgvector memories |

`python -m app.migrate` (the `migrate` service / Cloud Run job) is idempotent: it creates the `vector` extension and the
schemas, runs `alembic upgrade head`, and then runs the LangGraph checkpointer setup.

New migration after changing `api/app/models.py` (stack running):

```powershell
docker compose -f infra/docker-compose.yaml --env-file infra/.env run --rm migrate `
  alembic revision --autogenerate -m "describe change"
```

## Python without Docker

```powershell
uv sync --all-packages        # every service + dev tools into .venv
uv run pytest -q
uv run ruff check api mcp_server
```

Adding a dependency to one service: `uv add --package analyst-api <pkg>` (or `analyst-mcp`, `analyst-evals`).
Each Docker image installs only its own service's dependencies (`uv sync --frozen --package <name>`).

## Handy commands

```powershell
.\scripts\dev.ps1 logs api       # follow one service
.\scripts\dev.ps1 migrate        # re-run migrations
.\scripts\dev.ps1 reset          # stop and delete the local DB volume
docker compose -f infra/docker-compose.yaml --env-file infra/.env --profile inspector up mcp-inspector
#   -> http://localhost:6274, server URL http://mcp:8000/mcp (streamable HTTP)
```
