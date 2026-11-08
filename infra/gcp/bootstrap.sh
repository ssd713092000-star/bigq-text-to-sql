#!/usr/bin/env bash
# One-time (idempotent) GCP setup for the prod deployment. Run in Cloud Shell or any bash with gcloud:
#
#   GITHUB_REPO=owner/repo EXPORT_BUCKET=... EXAMPLES_BUCKET=... bash infra/gcp/bootstrap.sh
#
# Creates: APIs, Artifact Registry repo, service accounts + IAM, Secret Manager secrets,
# Workload Identity Federation for GitHub Actions, Private Google Access on the VPC subnet.
# Cloud SQL is separate: infra/gcp/setup_cloudsql.sh.  Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck source=config.sh
source ./config.sh

require PROJECT_ID REGION GITHUB_REPO
[[ "$GITHUB_REPO" == */* ]] || die "GITHUB_REPO must look like owner/repo"
gcloud config set project "$PROJECT_ID" >/dev/null
PROJECT_NUMBER="$(project_number)"

# ---------------------------------------------------------------- APIs
log "Enabling APIs (takes a minute the first time)"
gcloud services enable \
  run.googleapis.com artifactregistry.googleapis.com sqladmin.googleapis.com secretmanager.googleapis.com \
  iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com cloudresourcemanager.googleapis.com \
  compute.googleapis.com aiplatform.googleapis.com bigquery.googleapis.com storage.googleapis.com
# RAG Engine Serverless mode needs the Vector Search API; enable separately so a name change can't abort the run
gcloud services enable vectorsearch.googleapis.com 2>/dev/null \
  || warn "Could not enable vectorsearch.googleapis.com: enable 'Vector Search API' in the console if RAG import fails"
ok "APIs enabled"

# ---------------------------------------------------------------- Artifact Registry
log "Artifact Registry: ${REGION}/${AR_REPO}"
if ! gcloud artifacts repositories describe "$AR_REPO" --location "$REGION" >/dev/null 2>&1; then
  gcloud artifacts repositories create "$AR_REPO" --location "$REGION" --repository-format=docker \
    --description="Text-to-SQL analyst images"
fi
# keep the 15 newest images per service; delete untagged ones older than 7 days
cat > /tmp/ar-cleanup.json <<'EOF'
[
  {"name": "keep-recent", "action": {"type": "Keep"}, "mostRecentVersions": {"keepCount": 15}},
  {"name": "delete-old-untagged", "action": {"type": "Delete"},
   "condition": {"tagState": "UNTAGGED", "olderThan": "604800s"}}
]
EOF
gcloud artifacts repositories set-cleanup-policies "$AR_REPO" --location "$REGION" \
  --policy=/tmp/ar-cleanup.json --no-dry-run >/dev/null
ok "${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}"

# ---------------------------------------------------------------- service accounts
log "Service accounts"
for sa in sa-web sa-api sa-mcp sa-migrate sa-deployer; do
  email="${sa}@${PROJECT_ID}.iam.gserviceaccount.com"
  gcloud iam service-accounts describe "$email" >/dev/null 2>&1 || \
    gcloud iam service-accounts create "$sa" --display-name="analyst ${sa#sa-}"
  ok "$email"
done

project_role() {  # project_role <sa-email> <role>
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$1" --role="$2" \
    --condition=None >/dev/null
}

log "IAM: runtime identities"
# api: Cloud SQL, Vertex (Gemini/Claude/embeddings/RAG for examples + Mem0)
project_role "$SA_API" roles/cloudsql.client
project_role "$SA_API" roles/aiplatform.user
# mcp: Cloud SQL (dataset allowlist), Vertex RAG retrieval
project_role "$SA_MCP" roles/cloudsql.client
project_role "$SA_MCP" roles/aiplatform.user
# mcp signs export URLs as itself (IAM signBlob): it needs Token Creator on its own account
gcloud iam service-accounts add-iam-policy-binding "$SA_MCP" --member="serviceAccount:$SA_MCP" \
  --role=roles/iam.serviceAccountTokenCreator >/dev/null
# migrate job: Cloud SQL only
project_role "$SA_MIGRATE" roles/cloudsql.client
ok "runtime roles granted"

if [ -n "$EXPORT_BUCKET" ]; then
  # BigQuery writes exports as the signed-in user; mcp lists + signs them with its own identity
  gcloud storage buckets add-iam-policy-binding "gs://$EXPORT_BUCKET" \
    --member="serviceAccount:$SA_MCP" --role=roles/storage.objectViewer >/dev/null
  ok "sa-mcp can read gs://$EXPORT_BUCKET"
else
  warn "EXPORT_BUCKET not set: skipped export bucket grant"
fi
if [ -n "$EXAMPLES_BUCKET" ]; then
  gcloud storage buckets add-iam-policy-binding "gs://$EXAMPLES_BUCKET" \
    --member="serviceAccount:$SA_API" --role=roles/storage.objectAdmin >/dev/null
  gcloud storage buckets add-iam-policy-binding "gs://$EXAMPLES_BUCKET" \
    --member="serviceAccount:$SA_MCP" --role=roles/storage.objectViewer >/dev/null
  # RAG Engine's service agent reads example files to index them
  gcloud beta services identity create --service=aiplatform.googleapis.com >/dev/null 2>&1 || true
  gcloud storage buckets add-iam-policy-binding "gs://$EXAMPLES_BUCKET" \
    --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-vertex-rag.iam.gserviceaccount.com" \
    --role=roles/storage.objectViewer >/dev/null 2>&1 \
    || warn "RAG service agent not created yet: re-run after the first RAG corpus exists"
  ok "examples bucket grants on gs://$EXAMPLES_BUCKET"
else
  warn "EXAMPLES_BUCKET not set: skipped examples bucket grants"
fi

log "IAM: GitHub Actions deployer"
project_role "$SA_DEPLOYER" roles/run.admin                 # deploy services/jobs, set invoker IAM
project_role "$SA_DEPLOYER" roles/artifactregistry.writer   # push images
for runtime in "$SA_WEB" "$SA_API" "$SA_MCP" "$SA_MIGRATE"; do  # deploy "as" each runtime SA (scoped, not project-wide)
  gcloud iam service-accounts add-iam-policy-binding "$runtime" --member="serviceAccount:$SA_DEPLOYER" \
    --role=roles/iam.serviceAccountUser >/dev/null
done
ok "deployer can push and deploy"

# ---------------------------------------------------------------- secrets
log "Secret Manager"
ensure_secret "$SECRET_DB_PASSWORD"            # value written by setup_cloudsql.sh
ensure_secret "$SECRET_OAUTH_CLIENT_SECRET" "${GOOGLE_CLIENT_SECRET:-}"
secret_has_version "$SECRET_AUTH_SECRET" || ensure_secret "$SECRET_AUTH_SECRET" "$(openssl rand -base64 32)"
# Fernet key = urlsafe base64 of 32 random bytes
secret_has_version "$SECRET_TOKEN_KEY" || \
  ensure_secret "$SECRET_TOKEN_KEY" "$(openssl rand -base64 32 | tr '+/' '-_')"

grant_secret "$SECRET_DB_PASSWORD" "$SA_API"
grant_secret "$SECRET_DB_PASSWORD" "$SA_MCP"
grant_secret "$SECRET_DB_PASSWORD" "$SA_MIGRATE"
grant_secret "$SECRET_TOKEN_KEY" "$SA_API"
grant_secret "$SECRET_OAUTH_CLIENT_SECRET" "$SA_API"
grant_secret "$SECRET_OAUTH_CLIENT_SECRET" "$SA_WEB"
grant_secret "$SECRET_AUTH_SECRET" "$SA_WEB"   # Auth.js cookie encryption: web only
secret_has_version "$SECRET_OAUTH_CLIENT_SECRET" || \
  warn "No OAuth client secret yet. Add it:  printf %s 'SECRET' | gcloud secrets versions add $SECRET_OAUTH_CLIENT_SECRET --data-file=-"

# ---------------------------------------------------------------- networking
if [ "$MCP_INGRESS" = internal ]; then
  log "VPC for internal-only mcp: ${VPC_NETWORK}/${VPC_SUBNET} (${REGION})"
  gcloud compute networks subnets describe "$VPC_SUBNET" --region "$REGION" >/dev/null 2>&1 \
    || die "Subnet $VPC_SUBNET not found in $REGION. Create a VPC/subnet or set VPC_NETWORK/VPC_SUBNET."
  gcloud compute networks subnets update "$VPC_SUBNET" --region "$REGION" --enable-private-ip-google-access >/dev/null
  # api uses all-traffic egress so mcp sees it as internal; NAT carries Cloud SQL public IP + non-Google calls
  gcloud compute routers describe analyst-router --region "$REGION" >/dev/null 2>&1 || \
    gcloud compute routers create analyst-router --region "$REGION" --network "$VPC_NETWORK" >/dev/null
  gcloud compute routers nats describe analyst-nat --router analyst-router --region "$REGION" >/dev/null 2>&1 || \
    gcloud compute routers nats create analyst-nat --router analyst-router --region "$REGION" \
      --auto-allocate-nat-external-ips --nat-all-subnet-ip-ranges >/dev/null
  ok "Private Google Access + Cloud NAT ready (Cloud NAT is billed hourly + per GB)"
else
  log "Networking: mcp is IAM-protected (MCP_INGRESS=all); no VPC or NAT needed"
fi

# ---------------------------------------------------------------- Workload Identity Federation
log "Workload Identity Federation for github.com/${GITHUB_REPO}"
POOL=github
PROVIDER=github-oidc
gcloud iam workload-identity-pools describe "$POOL" --location=global >/dev/null 2>&1 || \
  gcloud iam workload-identity-pools create "$POOL" --location=global --display-name="GitHub Actions"
if ! gcloud iam workload-identity-pools providers describe "$PROVIDER" --location=global \
     --workload-identity-pool="$POOL" >/dev/null 2>&1; then
  gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" --location=global \
    --workload-identity-pool="$POOL" --display-name="GitHub OIDC" \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
    --attribute-condition="assertion.repository == '${GITHUB_REPO}'"
fi
gcloud iam service-accounts add-iam-policy-binding "$SA_DEPLOYER" --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/attribute.repository/${GITHUB_REPO}" \
  >/dev/null
WIF_PROVIDER="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/providers/${PROVIDER}"
ok "$WIF_PROVIDER"

# ---------------------------------------------------------------- summary
cat <<EOF

────────────────────────────────────────────────────────────────────────────
Done. Add these as GitHub repository VARIABLES (Settings > Secrets and variables > Actions > Variables),
or run:  bash infra/gcp/github_vars.sh   (needs the gh CLI)

  GCP_PROJECT_ID       = ${PROJECT_ID}
  GCP_PROJECT_NUMBER   = ${PROJECT_NUMBER}
  GCP_REGION           = ${REGION}
  GCP_WIF_PROVIDER     = ${WIF_PROVIDER}
  GCP_DEPLOYER_SA      = ${SA_DEPLOYER}
  CLOUDSQL_INSTANCE    = ${PROJECT_ID}:${REGION}:${SQL_INSTANCE}
  EXPORT_BUCKET        = ${EXPORT_BUCKET:-<set me>}
  EXAMPLES_BUCKET      = ${EXAMPLES_BUCKET:-<set me>}
  GOOGLE_CLIENT_ID     = <your OAuth client id>
  MCP_INGRESS          = ${MCP_INGRESS}

Service URLs (deterministic):
  web  $(run_url "$SVC_WEB")
  api  $(run_url "$SVC_API")
  mcp  $(run_url "$SVC_MCP")   (only sa-api can invoke; ingress=${MCP_INGRESS})

OAuth client (APIs & Services > Credentials): add
  Authorized JavaScript origin: $(run_url "$SVC_WEB")
  Redirect URI:                 $(run_url "$SVC_WEB")/api/auth/callback/google

Next: bash infra/gcp/setup_cloudsql.sh
────────────────────────────────────────────────────────────────────────────
EOF
