# Deploying to Google Cloud (prod)

Each part is its own Cloud Run unit, built and deployed by its own GitHub Actions workflow:

| Unit | Kind | Workflow (runs on push to `main` when its paths change) | Access |
|---|---|---|---|
| `analyst-web` | service | `deploy-web.yml` (`web/**`) | public |
| `analyst-api` | service | `deploy-api.yml` (`api/**`, `pyproject.toml`, `uv.lock`) | public; app-level auth + CORS |
| `analyst-migrate` | job | run by `deploy-api.yml` **before** each api release | n/a |
| `analyst-mcp` | service | `deploy-mcp.yml` (`mcp_server/**`, `pyproject.toml`, `uv.lock`) | IAM: only `sa-api` can invoke |

Pull requests run `ci.yml`: ruff + pytest, web lint + build, and Docker builds (no push) for changed services.
Every workflow can also be run by hand (**Actions > workflow > Run workflow**).

```
push to main ─┬─ deploy-mcp: test → build → push → deploy → grant sa-api run.invoker
              ├─ deploy-api: test → build → push → migrate job (Cloud SQL schema) → deploy → smoke test
              └─ deploy-web: lint → build (API URL baked in) → push → deploy → smoke test
```

Region `asia-south1` (same as the BigQuery data). Images: `asia-south1-docker.pkg.dev/<project>/analyst/<service>:<git sha>`.

## One-time setup

Run these in **Cloud Shell** from a clone of the repo. All scripts are idempotent, so they're safe to re-run.

### 1. Push the repo to GitHub

The workflows only deploy from `main` of the repository you name in step 2.

### 2. Bootstrap GCP

```bash
GITHUB_REPO=<owner>/<repo> \
EXPORT_BUCKET=<export-bucket> EXAMPLES_BUCKET=<examples-bucket> \
GOOGLE_CLIENT_SECRET='<oauth client secret>' \
bash infra/gcp/bootstrap.sh
```

This creates:
- **APIs** for Cloud Run, Artifact Registry, Cloud SQL, Secret Manager, IAM/STS, Vertex AI, BigQuery, Storage.
- The **Artifact Registry** repo `analyst`, with a cleanup policy that keeps the 15 newest images.
- **Service accounts**:
  - `sa-web`: no GCP roles.
  - `sa-api`: Cloud SQL client, Vertex AI user, examples bucket object admin.
  - `sa-mcp`: Cloud SQL client, Vertex AI user, export/examples bucket viewer, and Token Creator on itself (so it can sign download URLs).
  - `sa-migrate`: Cloud SQL client.
  - `sa-deployer`: used by GitHub Actions. Cloud Run admin, Artifact Registry writer, and `serviceAccountUser` on the four runtime accounts only.
- **Secret Manager**:
  - `db-password`: filled in by step 3.
  - `google-oauth-client-secret`.
  - `auth-secret` and `token-encryption-key`: generated.

  Each runtime account can read only the secrets it needs.
- **Workload Identity Federation**: a pool named `github` whose provider accepts **only** `<owner>/<repo>`.
  GitHub never holds a service-account key.

It prints the GitHub variables and the OAuth URLs to configure.

### 3. Cloud SQL

```bash
bash infra/gcp/setup_cloudsql.sh
```

Details: [cloudsql.md](cloudsql.md).

### 4. GitHub repository variables

**Settings > Secrets and variables > Actions > Variables** (or `bash infra/gcp/github_vars.sh` with the `gh` CLI):

| Variable | Example |
|---|---|
| `GCP_PROJECT_ID` | `project-16e0643c-a78c-4dd8-89b` |
| `GCP_PROJECT_NUMBER` | `<from bootstrap output>` |
| `GCP_REGION` | `asia-south1` |
| `GCP_WIF_PROVIDER` | `projects/<number>/locations/global/workloadIdentityPools/github/providers/github-oidc` |
| `GCP_DEPLOYER_SA` | `sa-deployer@<project>.iam.gserviceaccount.com` |
| `CLOUDSQL_INSTANCE` | `<project>:asia-south1:analyst-pg` |
| `EXPORT_BUCKET`, `EXAMPLES_BUCKET` | bucket names |
| `GOOGLE_CLIENT_ID` | OAuth client id |
| `MCP_INGRESS` | `all` (default) or `internal` |

No GitHub **secrets** are needed. Then create a GitHub **environment** named `prod` (Settings > Environments).
Add required reviewers there if deploys should wait for approval.

### 5. OAuth client

In **APIs & Services > Credentials**, open your OAuth 2.0 web client and add:
- Authorized JavaScript origin: `https://analyst-web-<project-number>.asia-south1.run.app`
- Authorized redirect URI: `https://analyst-web-<project-number>.asia-south1.run.app/api/auth/callback/google`

### 6. First deploy

Push to `main`, or run the three workflows by hand in this order: **deploy-mcp**, then **deploy-api** (runs the
migrate job first, which creates the tables), then **deploy-web**. After that, order doesn't matter: every URL is
deterministic (`https://<service>-<project-number>.<region>.run.app`), so each service already knows the others' addresses.

## How config reaches the services

- **Plain settings**: `infra/cloudrun/<service>.env.yaml`. `${...}` placeholders are filled in from GitHub variables by
  `envsubst` in the workflow, then passed with `--env-vars-file`. Change a setting there and push.
- **Secrets**: `--set-secrets NAME=secret:latest`. Values never touch GitHub or the repo. A new secret version
  takes effect on the next deploy.
- **Browser API URL**: `NEXT_PUBLIC_API_URL` is inlined into the web bundle at build time (Docker build arg).

## MCP access (`MCP_INGRESS`)

The default, `all`, means `analyst-mcp` has a public URL but `--no-allow-unauthenticated`, and only `sa-api` holds
`roles/run.invoker`. Google's front end rejects every other caller before the request reaches the container. The api
authenticates with a Google ID token in `X-Serverless-Authorization`. That leaves the `Authorization` header free for
the **user's** Google token, which the MCP server uses to run BigQuery as that user (phase 3).

With `internal`, the service also refuses non-VPC traffic. For that, the api routes all egress through the VPC
(`--vpc-egress all-traffic`). `bootstrap.sh` then enables Private Google Access and creates **Cloud NAT**, so Cloud SQL's
public IP and any non-Google endpoint stay reachable. NAT is billed hourly, which is why `internal` isn't the default.
To switch, set the variable, re-run `bootstrap.sh` with `MCP_INGRESS=internal`, and redeploy mcp and api.

## Rollback

```bash
gcloud run revisions list --service analyst-api --region asia-south1
gcloud run services update-traffic analyst-api --region asia-south1 --to-revisions <revision>=100
```

Database migrations are forward-only in prod. Write them so the previous api revision still works
(add columns/tables first, remove them in a later release).

## Org policy gotchas

- **"Domain restricted sharing"** (`iam.allowedPolicyMemberDomains`) blocks `--allow-unauthenticated` (`allUsers`).
  If the web/api deploy fails on the IAM step, ask an org admin to exempt the project, or put the services behind a load balancer + IAP.
- **Workload Identity Federation** must be allowed for `token.actions.githubusercontent.com`
  (`iam.workloadIdentityPoolProviders`) in locked-down orgs.
