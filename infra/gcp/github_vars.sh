#!/usr/bin/env bash
# Set the GitHub Actions repository variables the deploy workflows read. Needs the gh CLI, logged in.
#   GITHUB_REPO=owner/repo GOOGLE_CLIENT_ID=... EXPORT_BUCKET=... EXAMPLES_BUCKET=... bash infra/gcp/github_vars.sh
# Nothing here is secret: secrets live in Secret Manager, and GitHub authenticates with WIF (no keys).
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck source=config.sh
source ./config.sh
require PROJECT_ID REGION GITHUB_REPO
command -v gh >/dev/null || die "gh CLI not found (https://cli.github.com) - set the variables in the GitHub UI instead"

PROJECT_NUMBER="$(project_number)"
set_var() { gh variable set "$1" --repo "$GITHUB_REPO" --body "$2" >/dev/null && ok "$1"; }

log "GitHub variables on ${GITHUB_REPO}"
set_var GCP_PROJECT_ID     "$PROJECT_ID"
set_var GCP_PROJECT_NUMBER "$PROJECT_NUMBER"
set_var GCP_REGION         "$REGION"
set_var GCP_WIF_PROVIDER   "projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github/providers/github-oidc"
set_var GCP_DEPLOYER_SA    "$SA_DEPLOYER"
set_var CLOUDSQL_INSTANCE  "${PROJECT_ID}:${REGION}:${SQL_INSTANCE}"
set_var MCP_INGRESS        "$MCP_INGRESS"
for name in GOOGLE_CLIENT_ID EXPORT_BUCKET EXAMPLES_BUCKET; do
  if [ -n "${!name:-}" ]; then set_var "$name" "${!name}"; else warn "$name not set"; fi
done
