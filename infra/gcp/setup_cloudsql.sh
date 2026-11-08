#!/usr/bin/env bash
# Cloud SQL for PostgreSQL: instance, database, app user (password in Secret Manager). Idempotent.
#
#   bash infra/gcp/setup_cloudsql.sh                 # create/verify instance, database, user
#   RUN_MIGRATE=1 bash infra/gcp/setup_cloudsql.sh   # ...then run the analyst-migrate Cloud Run job (after first deploy)
#
# Tables are NOT created here. They come from `python -m app.migrate` (Cloud Run job analyst-migrate, run by
# the api deploy workflow before every api release): pgvector extension, schemas app/checkpoints/mem0, Alembic,
# and LangGraph's AsyncPostgresSaver.setup(). For a manual alternative see infra/gcp/sql/cloudsql_init.sql
# and docs/cloudsql.md.
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck source=config.sh
source ./config.sh

require PROJECT_ID REGION SQL_INSTANCE DB_NAME DB_USER
gcloud config set project "$PROJECT_ID" >/dev/null
gcloud services enable sqladmin.googleapis.com secretmanager.googleapis.com >/dev/null

# ---------------------------------------------------------------- instance
log "Cloud SQL instance ${SQL_INSTANCE} (${SQL_VERSION}, ${SQL_EDITION}, ${SQL_TIER}, ${REGION})"
if gcloud sql instances describe "$SQL_INSTANCE" >/dev/null 2>&1; then
  ok "already exists ($(gcloud sql instances describe "$SQL_INSTANCE" --format='value(state)'))"
else
  warn "creating: this takes 5-10 minutes"
  # Public IP with NO authorized networks: the only way in is the Cloud SQL connector / Auth Proxy
  # (Cloud Run's built-in connection), which authenticates with IAM and encrypts traffic.
  gcloud sql instances create "$SQL_INSTANCE" \
    --database-version="$SQL_VERSION" \
    --edition="$SQL_EDITION" \
    --tier="$SQL_TIER" \
    --region="$REGION" \
    --availability-type="$SQL_AVAILABILITY" \
    --storage-type=SSD \
    --storage-size="${SQL_STORAGE_GB}GB" \
    --storage-auto-increase \
    --backup-start-time=18:30 \
    --retained-backups-count=7 \
    --enable-point-in-time-recovery \
    --ssl-mode=ENCRYPTED_ONLY \
    --insights-config-query-insights-enabled \
    --deletion-protection
  ok "instance created"
fi
CONNECTION_NAME="$(gcloud sql instances describe "$SQL_INSTANCE" --format='value(connectionName)')"

# ---------------------------------------------------------------- database
log "Database ${DB_NAME}"
if gcloud sql databases describe "$DB_NAME" --instance "$SQL_INSTANCE" >/dev/null 2>&1; then
  ok "already exists"
else
  gcloud sql databases create "$DB_NAME" --instance "$SQL_INSTANCE" >/dev/null
  ok "created"
fi

# ---------------------------------------------------------------- user + password secret
log "Database user ${DB_USER} (password in Secret Manager: ${SECRET_DB_PASSWORD})"
ensure_secret "$SECRET_DB_PASSWORD"
users="$(gcloud sql users list --instance "$SQL_INSTANCE" --format='value(name)')"
user_exists=$(grep -x -- "$DB_USER" <<<"$users" >/dev/null && echo 1 || echo 0)

if [ "$user_exists" = 1 ] && secret_has_version "$SECRET_DB_PASSWORD"; then
  ok "user and password secret already exist (rotate: ROTATE_PASSWORD=1)"
fi
if [ "$user_exists" = 0 ] || ! secret_has_version "$SECRET_DB_PASSWORD" || [ "${ROTATE_PASSWORD:-0}" = 1 ]; then
  # URL-safe password; the app builds connection strings with proper escaping, but this keeps psql easy too
  PASSWORD="$(openssl rand -base64 36 | tr -d '\n' | tr '+/' '-_')"
  if [ "$user_exists" = 1 ]; then
    gcloud sql users set-password "$DB_USER" --instance "$SQL_INSTANCE" --password="$PASSWORD" >/dev/null
    ok "password reset"
  else
    # Users created through the Admin API are members of cloudsqlsuperuser: they can CREATE EXTENSION vector,
    # create schemas and own the tables, which is what the migrate job needs.
    gcloud sql users create "$DB_USER" --instance "$SQL_INSTANCE" --password="$PASSWORD" >/dev/null
    ok "user created"
  fi
  ensure_secret "$SECRET_DB_PASSWORD" "$PASSWORD"
  unset PASSWORD
  warn "Running Cloud Run revisions keep the old password until redeployed (secret version: latest)"
fi

# runtime identities that connect to the DB
for sa in "$SA_API" "$SA_MCP" "$SA_MIGRATE"; do
  if gcloud iam service-accounts describe "$sa" >/dev/null 2>&1; then
    grant_secret "$SECRET_DB_PASSWORD" "$sa"
  else
    warn "$sa missing: run bootstrap.sh first"
  fi
done

# ---------------------------------------------------------------- optional: apply schema now
if [ "${RUN_MIGRATE:-0}" = 1 ]; then
  log "Running Cloud Run job ${JOB_MIGRATE}"
  gcloud run jobs describe "$JOB_MIGRATE" --region "$REGION" >/dev/null 2>&1 \
    || die "Job $JOB_MIGRATE not deployed yet: push to main (or run the 'deploy-api' workflow) first"
  gcloud run jobs execute "$JOB_MIGRATE" --region "$REGION" --wait
  ok "schema is up to date"
fi

cat <<EOF

────────────────────────────────────────────────────────────────────────────
Cloud SQL ready.
  connection name   ${CONNECTION_NAME}
  database / user   ${DB_NAME} / ${DB_USER}
  password          Secret Manager: ${SECRET_DB_PASSWORD}

Cloud Run settings (the deploy workflows already use these):
  --add-cloudsql-instances=${CONNECTION_NAME}
  DB_HOST=/cloudsql/${CONNECTION_NAME}   DB_NAME=${DB_NAME}   DB_USER=${DB_USER}
  --set-secrets=DB_PASSWORD=${SECRET_DB_PASSWORD}:latest

GitHub variable:  CLOUDSQL_INSTANCE=${CONNECTION_NAME}

Tables: created by the analyst-migrate job on the first api deploy.
Manual alternative: docs/cloudsql.md ("Create the tables by hand").
────────────────────────────────────────────────────────────────────────────
EOF
