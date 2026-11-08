"""One command to evaluate several Vertex AI models on the same 20 questions.

Edit MODELS below, then:

    uv run evals/run_all.py

It will:
  1. build golden.jsonl if it's missing
  2. check the MCP server is reachable
  3. pick the same 20 questions for every model (spread across all categories, saved for reuse)
  4. run every model x config
  5. score every run with DeepEval (+ LLM judge unless USE_LLM_JUDGE = False)
  6. print and save a side-by-side comparison

Options: --models gemini-3.8-flash gemini-3.5-flash-lite   (overrides MODELS)
         --per-category 1   --n 20   --configs rag no_rag   --no-llm   --judge gemini-3.8-flash   --new-sample

Models and questions run one after another.
"""

from __future__ import annotations

# ════════════════════ EDIT THESE ════════════════════
MODELS = [
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.1-pro-preview",
    # "claude-sonnet-4-5@20250929",   # Claude on Vertex: use the exact id from Model Garden
]
PER_CATEGORY = 1                 # 1 question of each type -> 7 questions per model. Set to None to use QUESTIONS_PER_MODEL
QUESTIONS_PER_MODEL = 20         # used only when PER_CATEGORY is None
CONFIGS = ["rag"]                # add "no_rag" to measure what the RAG examples add
USE_LLM_JUDGE = True
JUDGE_MODEL = "gemini-3.8-flash"  # ideally different from the models under test
# ════════════════════════════════════════════════════

import argparse
import asyncio
import csv
import datetime as dt
import json
import os
import random
import subprocess
import sys
import time
from collections import defaultdict

import httpx

from common import DATASET, GOLDEN, ROOT, load_env, read_jsonl
import run_agent
import score

SAMPLE_FILE = ROOT / "results" / "question_sample.json"  # base name; mode is added to it
COMPARE_DIR = ROOT / "results" / "comparisons"

# share of the sample per category (keeps every kind of question represented)
CATEGORY_WEIGHTS = {"easy": 5, "medium": 5, "hard": 4, "relative_date": 2,
                    "ambiguous": 2, "unanswerable": 1, "off_topic": 1}


def pick_questions(items: list[dict], n: int, new_sample: bool, per_category: int | None = None) -> list[dict]:
    """Same questions for every model and every re-run, unless --new-sample.
    per_category=k -> k questions from each category; otherwise n questions spread by CATEGORY_WEIGHTS."""
    by_id = {i["id"]: i for i in items}
    mode = f"per_category:{per_category}" if per_category else f"n:{n}"
    sample_file = SAMPLE_FILE.with_name(f"question_sample_{mode.replace(':', '_')}.json")
    if sample_file.exists() and not new_sample:
        saved = json.loads(sample_file.read_text())
        if all(i in by_id for i in saved["ids"]):
            print(f"Using saved question sample ({mode}, {len(saved['ids'])} questions): {sample_file.name}")
            return [by_id[i] for i in saved["ids"]]

    by_cat = defaultdict(list)
    for it in items:
        by_cat[it["category"]].append(it)
    if per_category:
        quota = {c: per_category for c in by_cat}
    else:
        total_w = sum(CATEGORY_WEIGHTS.get(c, 1) for c in by_cat)
        quota = {c: max(1, round(n * CATEGORY_WEIGHTS.get(c, 1) / total_w)) for c in by_cat}
        while sum(quota.values()) > n:  # trim rounding overflow from the biggest buckets
            quota[max(quota, key=quota.get)] -= 1
        while sum(quota.values()) < n:
            quota[max(by_cat, key=lambda c: len(by_cat[c]) - quota[c])] += 1

    rng = random.Random(42)
    chosen = []
    for cat, k in quota.items():
        chosen += rng.sample(by_cat[cat], min(k, len(by_cat[cat])))
    chosen.sort(key=lambda i: i["id"])

    sample_file.parent.mkdir(parents=True, exist_ok=True)
    sample_file.write_text(json.dumps({"mode": mode, "created": dt.datetime.now().isoformat(timespec="seconds"),
                                       "ids": [i["id"] for i in chosen]}, indent=2))
    counts = defaultdict(int)
    for c in chosen:
        counts[c["category"]] += 1
    print(f"New question sample ({mode}, {len(chosen)} questions): {dict(counts)} -> {sample_file.name}")
    return chosen


async def run_models(models: list[str], configs: list[str], items: list[dict], mcp_url: str, oauth: bool,
                     timeout: float) -> list[str]:
    """Run every model x config, one after another."""
    run_files = []
    for model in models:
        for cfg in configs:
            try:
                run_files.append(await run_agent.run(model, cfg, items, mcp_url, oauth, timeout))
            except Exception as e:  # e.g. wrong model id / no access: skip, keep the batch going
                print(f"!! {model} [{cfg}] failed: {type(e).__name__}: {e}")
    return run_files


def ensure_golden() -> None:
    if GOLDEN.exists():
        return
    print("golden.jsonl not found - building expected results first...")
    subprocess.run([sys.executable, str(ROOT / "build_golden.py")], check=True)


def check_server(url: str) -> None:
    try:
        httpx.get(url, timeout=5)  # any HTTP response (even 4xx) means the server is up
    except httpx.HTTPError:
        sys.exit(f"MCP server not reachable at {url}. Start it first:  uv run bigq_mcp/bq_mcp_server.py")


def print_comparison(summaries: list[dict], batch: str) -> None:
    cols = [("model", 30), ("config", 7), ("execution_accuracy", 9), ("exec_acc_easy", 6), ("exec_acc_medium", 6),
            ("exec_acc_hard", 6), ("answer_correctness", 8), ("faithfulness", 8), ("behavior", 8),
            ("workflow", 8), ("avg_latency_s", 8), ("avg_gb_per_question", 8), ("errors", 6)]
    labels = {"execution_accuracy": "exec_acc", "exec_acc_easy": "easy", "exec_acc_medium": "medium",
              "exec_acc_hard": "hard", "answer_correctness": "correct", "faithfulness": "faithful",
              "behavior": "behavior", "workflow": "workflow", "avg_latency_s": "lat_s",
              "avg_gb_per_question": "gb/q"}

    def fmt(v):
        if v is None or v == "":
            return "-"
        return f"{v:.2f}" if isinstance(v, float) else str(v)

    rows = sorted(summaries, key=lambda s: -(s.get("execution_accuracy") or 0))
    print("\n" + "═" * 140)
    print(" ".join(labels.get(c, c)[:w].ljust(w) for c, w in cols))
    print("─" * 140)
    for s in rows:
        print(" ".join(fmt(s.get(c))[:w].ljust(w) for c, w in cols))
    print("═" * 140)

    COMPARE_DIR.mkdir(parents=True, exist_ok=True)
    out = COMPARE_DIR / f"comparison_{batch}.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"Comparison saved -> {out}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=MODELS)
    ap.add_argument("--n", type=int, default=QUESTIONS_PER_MODEL)
    ap.add_argument("--per-category", type=int, default=PER_CATEGORY,
                    help="k questions from each category instead of --n (e.g. 1 = one of each type)")
    ap.add_argument("--configs", nargs="+", default=CONFIGS)
    ap.add_argument("--no-llm", action="store_true", help="skip the LLM judge (free, deterministic only)")
    ap.add_argument("--judge", default=JUDGE_MODEL)
    ap.add_argument("--new-sample", action="store_true", help="draw a new set of questions")
    ap.add_argument("--timeout", type=float, default=180)
    ap.add_argument("--oauth", action="store_true")
    args = ap.parse_args()

    load_env()
    mcp_url = os.getenv("MCP_URL", "http://localhost:8000/mcp")
    ensure_golden()
    check_server(mcp_url)

    items = pick_questions(read_jsonl(GOLDEN if GOLDEN.exists() else DATASET), args.n, args.new_sample,
                           args.per_category)
    judge = None if (args.no_llm or not USE_LLM_JUDGE) else args.judge
    batch = dt.datetime.now().strftime("%Y%m%d-%H%M%S")

    print(f"\nBatch {batch}: {len(args.models)} model(s) x {len(args.configs)} config(s) x {len(items)} questions"
          f" | judge: {judge or 'none'}")

    t0 = time.perf_counter()
    run_files = asyncio.run(run_models(args.models, args.configs, items, mcp_url, args.oauth, args.timeout))
    print(f"\nAgent runs finished in {time.perf_counter() - t0:.0f}s")

    summaries = []
    for rf in run_files:
        try:
            summaries.append(score.score_run(score.Path(rf), judge))
        except SystemExit as e:
            print(f"!! skipped scoring {rf}: {e}")

    if summaries:
        print_comparison(summaries, batch)
        print(f"Full history: {score.LEADERBOARD}")


if __name__ == "__main__":
    main()