# Cloud SQL (PostgreSQL) setup

One Cloud SQL for PostgreSQL 17 instance holds everything the app stores, in one database (`analyst`) with three schemas:

| Schema | Created by | Tables |
|---|---|---|
| `app` | Alembic (`api/migrations`) | `orgs`, `users`, `user_credentials`, `datasets`, `dataset_tables`, `dataset_notes`, `rag_corpora`, `examples`, `chats`, `query_log`, `alembic_version` |
| `checkpoints` | LangGraph `AsyncPostgresSaver.setup()` | `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations` |
| `mem0` | Mem0 (phase 7) | memory vectors (pgvector) |

`create_agent(..., checkpointer=AsyncPostgresSaver)` stores every chat's state in `checkpoints` under
`thread_id = "<email>:<chat_id>"`. The app connects with `search_path=checkpoints` for the saver, so its tables
stay separate from the app tables.

---

## Option A: script (recommended)

Run in **Cloud Shell** (already has gcloud and is signed in), from a clone of this repo:

```bash
bash infra/gcp/bootstrap.sh          # once: APIs, service accounts, secrets, WIF (see docs/deploy.md)
bash infra/gcp/setup_cloudsql.sh     # instance + database + user; password goes to Secret Manager
```

`setup_cloudsql.sh` is idempotent. It:

1. Creates instance `analyst-pg`: `POSTGRES_17`, **Enterprise** edition, `db-custom-1-3840` (1 vCPU, 3.75 GB),
   zonal, 10 GB SSD with auto-grow, daily backups (7 kept) + point-in-time recovery, query insights,
   deletion protection, `ssl-mode=ENCRYPTED_ONLY`.
   It has a public IP with **no authorized networks**, so the only way in is the Cloud SQL connector / Auth Proxy,
   which checks IAM (`roles/cloudsql.client`) and encrypts traffic. Cloud Run's built-in Cloud SQL connection uses that path.
2. Creates database `analyst`.
3. Creates user `analyst` with a random password and stores it in Secret Manager as `db-password`
   (`ROTATE_PASSWORD=1` to rotate; redeploy api/mcp afterwards).
4. Grants `sa-api`, `sa-mcp`, `sa-migrate` access to that secret.

Settings live in [infra/gcp/config.sh](../infra/gcp/config.sh) (`SQL_TIER`, `SQL_EDITION`, `SQL_AVAILABILITY`, ...).
For high availability set `SQL_AVAILABILITY=REGIONAL` (about 2x cost).

**The tables** are created by the `analyst-migrate` Cloud Run job, which the `deploy-api` workflow runs before
every api release. It's idempotent: extension `vector`, the three schemas, `alembic upgrade head`, then
LangGraph's checkpointer setup. To run it by hand after the first deploy:

```bash
RUN_MIGRATE=1 bash infra/gcp/setup_cloudsql.sh
# or: gcloud run jobs execute analyst-migrate --region asia-south1 --wait
```

## Option B: console, step by step

1. **SQL > Create instance > PostgreSQL**. Edition **Enterprise** (Enterprise Plus is pre-selected for new
   versions and costs much more), version **PostgreSQL 17**, instance id `analyst-pg`, region **asia-south1**,
   zonal. Machine: 1 vCPU / 3.75 GB. Storage: SSD, 10 GB, automatic increases on.
2. **Connections**: Public IP on, **no** authorized networks. **Security**: "Allow only SSL connections".
3. **Data protection**: automated backups, point-in-time recovery, deletion protection.
4. Create the instance (5-10 min).
5. **Databases > Create database**: `analyst`.
6. **Users > Add user account > Built-in authentication**: `analyst`, strong password. (Users created here are
   members of `cloudsqlsuperuser`, which can `CREATE EXTENSION vector` and create schemas.)
7. **Secret Manager > Create secret** `db-password` with that password, and grant
   *Secret Manager Secret Accessor* to `sa-api`, `sa-mcp` and `sa-migrate`.
8. Tables: let the migrate job create them (above), or follow "Create the tables by hand" below.

---

## Create the tables by hand (optional)

[infra/gcp/sql/cloudsql_init.sql](../infra/gcp/sql/cloudsql_init.sql) is the exact DDL the migrate job applies,
generated from the code (`uv run --package analyst-api python scripts/gen_cloudsql_sql.py`). It's been tested:
applying it to an empty database and then running the migrate job changes nothing. It also records the
LangGraph migration versions, so `AsyncPostgresSaver.setup()` treats them as done.

Use `psql` through the **Cloud SQL Auth Proxy** (the instance has no authorized networks):

```bash
# 1. proxy (Cloud Shell has it; elsewhere: https://cloud.google.com/sql/docs/postgres/sql-proxy#install)
cloud-sql-proxy project-16e0643c-a78c-4dd8-89b:asia-south1:analyst-pg --port 5433 &

# 2. apply (psql's default autocommit is required: the checkpoint indexes use CREATE INDEX CONCURRENTLY)
export PGPASSWORD="$(gcloud secrets versions access latest --secret db-password)"
psql "host=127.0.0.1 port=5433 dbname=analyst user=analyst" -f infra/gcp/sql/cloudsql_init.sql
```

Don't paste the file into Cloud SQL Studio: it runs statements in a transaction, and `CREATE INDEX CONCURRENTLY`
fails inside one. The file is for an **empty** database. Afterwards, schema changes come from Alembic (the migrate job).

### Check the result

```sql
SELECT table_schema, count(*) FROM information_schema.tables
WHERE table_schema IN ('app', 'checkpoints') GROUP BY 1;        -- app 11, checkpoints 4
SELECT version_num FROM app.alembic_version;                   -- current Alembic head
SELECT string_agg(v::text, ',' ORDER BY v) FROM checkpoints.checkpoint_migrations;   -- 0,...,9
SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';
```

---

## How the services connect

Cloud Run mounts the instance as a Unix socket (`--add-cloudsql-instances` / `--set-cloudsql-instances` for jobs):

| Env | Value |
|---|---|
| `DB_HOST` | `/cloudsql/project-16e0643c-a78c-4dd8-89b:asia-south1:analyst-pg` |
| `DB_NAME` / `DB_USER` | `analyst` / `analyst` |
| `DB_PASSWORD` | Secret Manager `db-password:latest` (`--set-secrets`) |

`api/app/config.py` turns these into an asyncpg URL (SQLAlchemy, app tables) and a psycopg conninfo
(LangGraph checkpointer, Mem0) with correct escaping, for both a hostname (local) and the socket path (Cloud Run).

**Connections:** each api instance opens up to `DB_POOL_SIZE` (5) SQLAlchemy connections plus the checkpointer
pool. With `--max-instances 5` on api and mcp this stays well under the ~100 `max_connections` of a 3.75 GB instance.
If you raise max instances, raise the tier or lower the pools.

## Connect from your machine

```powershell
cloud-sql-proxy project-16e0643c-a78c-4dd8-89b:asia-south1:analyst-pg --port 5433
psql "host=127.0.0.1 port=5433 dbname=analyst user=analyst"
```

Your account needs `roles/cloudsql.client`. Local development normally uses the compose Postgres instead
(see [local-dev.md](local-dev.md)).

## Cost notes

- `db-custom-1-3840` Enterprise, zonal, 10 GB: the smallest dedicated-core size with an SLA. For a cheaper demo,
  `SQL_TIER=db-g1-small` (shared core, no SLA).
- Cloud SQL bills while the instance runs; stop it when idle: `gcloud sql instances patch analyst-pg --activation-policy NEVER`
  (`ALWAYS` to start it again).
