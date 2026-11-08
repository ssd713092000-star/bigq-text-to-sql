# Text-to-SQL Analyst

Ask questions about your BigQuery data in plain English. Users sign in with Google, register their own datasets, approve
every query before it runs, and get answers, tables, charts and download links. Queries run with the user's own Google
credentials, so BigQuery IAM decides what each person can see.

## Layout

| Path | What |
|---|---|
| `web/` | Next.js app (App Router, TypeScript, Tailwind) |
| `api/` | FastAPI: auth, datasets, examples, chat agent (LangChain + LangGraph), migrations |
| `mcp_server/` | FastMCP read-only BigQuery server (dry-run guards, byte caps, GCS export, verified-example search) |
| `evals/` | DeepEval suite (50 questions; model comparison) |
| `infra/` | Docker Compose, env template, GCP setup scripts |
| `docs/` | Setup and operations docs |
| `data_loader.ipynb` | Loads the NYC taxi demo data into BigQuery |

## Quick start

```powershell
Copy-Item infra\.env.example infra\.env   # fill it in
.\scripts\dev.ps1 up                       # http://localhost:3000
```

Details: [docs/local-dev.md](docs/local-dev.md).
