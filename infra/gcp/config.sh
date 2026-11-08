# Shared settings for infra/gcp/*.sh.  Edit here (or override with env vars) before running the scripts.
# shellcheck disable=SC2034  # sourced by the other scripts; variables are used there
# Everything is prod-only: one project, one region.

PROJECT_ID="${PROJECT_ID:-project-16e0643c-a78c-4dd8-89b}"
REGION="${REGION:-asia-south1}"                  # Cloud Run + Cloud SQL + Artifact Registry (BigQuery data is here)
RAG_LOCATION="${RAG_LOCATION:-us-central1}"      # Vertex RAG Engine Serverless mode
GEMINI_LOCATION="${GEMINI_LOCATION:-global}"
CLAUDE_LOCATION="${CLAUDE_LOCATION:-us-east5}"

# GitHub repository allowed to deploy (Workload Identity Federation). REQUIRED: owner/name, exactly as on GitHub.
GITHUB_REPO="${GITHUB_REPO:-ssd713092000-star/bigq-text-to-sql}"

# Artifact Registry
AR_REPO="${AR_REPO:-analyst}"

# Cloud Run names
SVC_WEB="${SVC_WEB:-analyst-web}"
SVC_API="${SVC_API:-analyst-api}"
SVC_MCP="${SVC_MCP:-analyst-mcp}"
JOB_MIGRATE="${JOB_MIGRATE:-analyst-migrate}"

# Service accounts (runtime identity per service + one for GitHub Actions)
SA_WEB="sa-web@${PROJECT_ID}.iam.gserviceaccount.com"
SA_API="sa-api@${PROJECT_ID}.iam.gserviceaccount.com"
SA_MCP="sa-mcp@${PROJECT_ID}.iam.gserviceaccount.com"
SA_MIGRATE="sa-migrate@${PROJECT_ID}.iam.gserviceaccount.com"
SA_DEPLOYER="sa-deployer@${PROJECT_ID}.iam.gserviceaccount.com"

# MCP access. Default "all" + IAM: the service rejects every caller except sa-api (run.invoker), checked by
# Google's front end before the container sees the request. "internal" also hides it from the internet, but
# then api must send ALL egress through the VPC (Direct VPC egress), which needs Cloud NAT for Cloud SQL's
# public IP and non-Google endpoints. Set the same value as the GitHub variable MCP_INGRESS.
MCP_INGRESS="${MCP_INGRESS:-all}"                # all | internal
VPC_NETWORK="${VPC_NETWORK:-default}"            # used only when MCP_INGRESS=internal
VPC_SUBNET="${VPC_SUBNET:-default}"

# Buckets (must already exist or be created by bootstrap; export bucket must be in the BigQuery location)
EXPORT_BUCKET="${EXPORT_BUCKET:-}"
EXAMPLES_BUCKET="${EXAMPLES_BUCKET:-}"

# Cloud SQL
SQL_INSTANCE="${SQL_INSTANCE:-analyst-pg}"
SQL_VERSION="${SQL_VERSION:-POSTGRES_17}"
SQL_EDITION="${SQL_EDITION:-ENTERPRISE}"         # ENTERPRISE_PLUS is the default for PG16+, and costs far more
SQL_TIER="${SQL_TIER:-db-custom-1-3840}"         # 1 vCPU / 3.75 GB. Cheapest shared-core: db-g1-small (no SLA)
SQL_AVAILABILITY="${SQL_AVAILABILITY:-ZONAL}"    # REGIONAL = HA standby (about 2x cost)
SQL_STORAGE_GB="${SQL_STORAGE_GB:-10}"
DB_NAME="${DB_NAME:-analyst}"
DB_USER="${DB_USER:-analyst}"

# Secret Manager secret names
SECRET_DB_PASSWORD="db-password"
SECRET_OAUTH_CLIENT_SECRET="google-oauth-client-secret"
SECRET_AUTH_SECRET="auth-secret"                 # Auth.js session encryption
SECRET_TOKEN_KEY="token-encryption-key"          # Fernet key for stored Google refresh tokens

# ---------- helpers ----------
log()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
ok()   { printf '    \033[32m%s\033[0m\n' "$*"; }
warn() { printf '    \033[33m%s\033[0m\n' "$*"; }
die()  { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

require() { for v in "$@"; do [ -n "${!v:-}" ] || die "$v is not set (infra/gcp/config.sh or env var)"; done; }

project_number() { gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)'; }

# Cloud Run URLs are deterministic: https://<service>-<project-number>.<region>.run.app
run_url() { echo "https://$1-$(project_number).${REGION}.run.app"; }

# Create a secret if missing. With a value, also adds it as a new version.
ensure_secret() {
  local name="$1" value="${2:-}"
  if ! gcloud secrets describe "$name" --project "$PROJECT_ID" >/dev/null 2>&1; then
    gcloud secrets create "$name" --project "$PROJECT_ID" --replication-policy=automatic >/dev/null
    ok "created secret $name"
  fi
  if [ -n "$value" ]; then
    printf '%s' "$value" | gcloud secrets versions add "$name" --project "$PROJECT_ID" --data-file=- >/dev/null
    ok "stored new version of $name"
  fi
}

secret_has_version() {
  [ -n "$(gcloud secrets versions list "$1" --project "$PROJECT_ID" --filter='state=ENABLED' --limit=1 --format='value(name)' 2>/dev/null)" ]
}

grant_secret() {  # grant_secret <secret> <service-account-email>
  gcloud secrets add-iam-policy-binding "$1" --project "$PROJECT_ID" \
    --member="serviceAccount:$2" --role=roles/secretmanager.secretAccessor --condition=None >/dev/null
}
