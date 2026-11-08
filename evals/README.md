# Text-to-SQL agent evals (DeepEval)

50 questions, expected answers computed from golden SQL, any Vertex AI model, results tagged by model.

## Files

| File | What it is |
|---|---|
| `dataset.jsonl` | 50 questions: id, category, golden SQL, value columns, expected behaviour |
| `build_golden.py` | Runs every golden SQL once and saves the expected results in `golden.jsonl` |
| `agent_factory.py` | System prompt, Vertex model factory (Gemini / Claude on Vertex), MCP connection |
| `run_agent.py` | Runs the agent on the questions; one trace per question |
| `metrics.py` | Deterministic metrics: Execution Accuracy, Workflow |
| `score.py` | DeepEval scoring (deterministic + LLM judge) and the leaderboard |
| `results/runs/` | `<timestamp>__<model>__<config>.jsonl` |
| `results/scores/` | `<run_id>__judge-<judge>.csv`, one row per question |
| `results/leaderboard.csv` | One row per scored run: compare models and configs here |

## Dataset (50)

| Category | Count | Expected behaviour |
|---|---|---|
| easy | 12 | correct numbers |
| medium | 15 | correct table |
| hard | 10 | correct table (windows, CTEs, ranking) |
| relative_date | 4 | "last month" etc. resolved against EVAL_TODAY = 2026-01-15 (Dec 2025) |
| ambiguous | 4 | asks a clarifying question, runs no SQL |
| unanswerable | 3 | says the data can't answer it |
| off_topic | 2 | briefly declines |

None of these are among the 20 RAG seed examples, so the agent can't just look the answers up.

## Metrics

| Metric | Type | Applies to |
|---|---|---|
| **Execution Accuracy** | deterministic | answer questions: same row count, and every golden value column matched by an agent column (any name/order, rounding tolerance, ratio vs percent accepted) |
| **Workflow** | deterministic | answer: searched examples first (rag), validated before running, ran a query, ≤3 failed validations, no save attempt. Behaviour: no SQL run |
| **Answer Correctness** | GEval (LLM judge) | answer text states the right numbers for the question |
| **Faithfulness** | DeepEval (LLM judge) | every claim in the answer is supported by the query result |
| **Behavior** | GEval (LLM judge) | clarify / unanswerable / decline handled as expected |

Judge: a Gemini model on Vertex (`--judge` or `JUDGE_MODEL`). Prefer a judge that differs from the model under test.

## Run it

```powershell
uv add deepeval "langchain[mcp]" langchain-google-genai "langchain-google-vertexai[anthropic]" google-cloud-bigquery python-dotenv

# 1. MCP server running (AUTH_MODE=none is simplest for unattended evals)
uv run bigq_mcp/bq_mcp_server.py

# 2. Expected results (once, and again after rebuilding the silver table)
uv run evals/build_golden.py

# 3. Run the agent - one or more Vertex models, one or more configs
uv run evals/run_agent.py --model gemini-3.8-flash --config rag no_rag
uv run evals/run_agent.py --model gemini-3.5-flash-lite
uv run evals/run_agent.py --model claude-sonnet-4-5@20250929        # Claude on Vertex: use your Model Garden id

# smoke test first
uv run evals/run_agent.py --model gemini-3.8-flash --ids e01 m06 h04 a01 u01

# 4. Score
uv run evals/score.py --all-unscored                 # every run not scored yet
uv run evals/score.py --no-llm                       # deterministic only, free
uv run evals/score.py --judge gemini-3.8-flash
```

## Settings (.env)

```ini
GOOGLE_CLOUD_PROJECT=project-16e0643c-a78c-4dd8-89b
GOOGLE_CLOUD_LOCATION=global
GEMINI_LOCATION=global          # Gemini endpoint
CLAUDE_LOCATION=us-east5        # Claude on Vertex region
JUDGE_MODEL=gemini-3.8-flash
MCP_URL=http://localhost:8000/mcp
EVAL_TODAY=2026-01-15           # keeps relative-date questions reproducible
```

## Notes

- Evals never save to RAG: `save_example` is removed from the agent's tools, and any save prompt is declined.
- `run_sql` approvals are auto-accepted by the eval client.
- `no_rag` removes `search_examples` so you can measure what the verified-example RAG adds.
- Each question runs in its own thread, so no memory carries between questions.
- Run files are written per question, so an interrupted run keeps its results.
- Golden results over 500 rows can't be checked (the agent gets a GCS link); `build_golden.py` warns about them.
