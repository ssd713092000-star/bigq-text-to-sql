"""Score agent runs with DeepEval and update the leaderboard.

    uv run evals/score.py                         # score the newest run file
    uv run evals/score.py results/runs/<run>.jsonl [...more runs]
    uv run evals/score.py --all-unscored          # every run without a scores file yet
    uv run evals/score.py --no-llm                # deterministic metrics only (free, fast)
    uv run evals/score.py --judge gemini-3.8-flash

Outputs (model name in every file name and row):
    results/scores/<run_id>__judge-<judge>.csv    one row per question
    results/leaderboard.csv                        one row per scored run, for comparing models
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import statistics
from pathlib import Path

os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")

from common import GOLDEN, LEADERBOARD, RUNS_DIR, SCORES_DIR, load_env, project, read_jsonl, slug
from metrics import ExecutionAccuracyMetric, WorkflowMetric

ANSWER_CATEGORIES = ["easy", "medium", "hard", "relative_date"]
BEHAVIOR_CATEGORIES = ["ambiguous", "unanswerable", "off_topic"]


# ───────────────────────── judge + LLM metrics ─────────────────────────
def build_judge(judge_model: str):
    from deepeval.models import GeminiModel

    return GeminiModel(model=judge_model, project=project(), use_vertexai=True, temperature=0,
                       location=os.getenv("GEMINI_LOCATION") or os.getenv("GOOGLE_CLOUD_LOCATION", "global"))


def llm_metrics(judge):
    from deepeval.metrics import FaithfulnessMetric, GEval
    from deepeval.test_case import SingleTurnParams as P

    answer_correctness = GEval(
        name="Answer Correctness",
        model=judge, threshold=0.7,
        evaluation_params=[P.INPUT, P.ACTUAL_OUTPUT, P.EXPECTED_OUTPUT],
        evaluation_steps=[
            "The expected output is the correct query result as JSON rows. The actual output is the agent's answer.",
            "Check that the key numbers in the actual output match the expected rows (allow rounding and formatting differences).",
            "Check that the actual output answers the question asked (right period, entity and metric).",
            "Penalise missing key figures, wrong numbers, or conclusions the rows don't support.",
            "Do not penalise extra context, assumptions, tables or SQL in the actual output.",
        ],
    )
    faithfulness = FaithfulnessMetric(model=judge, threshold=0.8, include_reason=True)
    behavior = GEval(
        name="Behavior",
        model=judge, threshold=0.7,
        evaluation_params=[P.INPUT, P.ACTUAL_OUTPUT, P.EXPECTED_OUTPUT],
        evaluation_steps=[
            "The expected output describes how the agent should behave for this question.",
            "Score high only if the actual output behaves that way: asks a short clarifying question, "
            "or says the data cannot answer it, or briefly declines an off-topic request.",
            "Score low if the actual output invents numbers, answers a guessed interpretation without asking, "
            "or complies with an off-topic request.",
        ],
    )
    return answer_correctness, faithfulness, behavior


# ───────────────────────── test cases ─────────────────────────
def build_cases(run_rows: list[dict], golden: dict[str, dict]):
    from deepeval.test_case import LLMTestCase, ToolCall

    answer_cases, behavior_cases = [], []
    for r in run_rows:
        g = golden.get(r["id"], {})
        meta = {
            "expected_behavior": r["expected_behavior"],
            "expected_rows": g.get("expected_rows"),
            "value_columns": g.get("value_columns", []),
            "result_rows": r.get("result_rows"),
            "tool_sequence": r.get("tool_sequence", []),
            "tools": r.get("tools", []),
            "n_validate_failures": r.get("n_validate_failures", 0),
        }
        common_args = dict(
            name=r["id"], input=r["question"], actual_output=r.get("answer") or "(no answer)",
            tools_called=[ToolCall(name=c["name"], input_parameters=c.get("args")) for c in r.get("tool_calls", [])],
            metadata=meta, completion_time=r.get("latency_s"),
        )
        if r["expected_behavior"] == "answer":
            expected = g.get("expected_rows")
            answer_cases.append(LLMTestCase(
                **common_args,
                expected_output=json.dumps(expected, default=str) if expected is not None
                else "(golden result missing - run build_golden.py)",
                retrieval_context=[json.dumps(r.get("result_rows"), default=str)] if r.get("result_rows")
                else ["(the agent ran no successful query)"],
            ))
        else:
            behavior_cases.append(LLMTestCase(**common_args, expected_output=g.get("notes") or r["expected_behavior"]))
    return answer_cases, behavior_cases


def run_deepeval(cases, metrics) -> dict[str, dict[str, dict]]:
    """Returns {case_name: {metric_name: {score, reason, success}}}."""
    if not cases:
        return {}
    from deepeval import evaluate
    from deepeval.evaluate.configs import AsyncConfig, DisplayConfig, ErrorConfig

    result = evaluate(
        test_cases=cases, metrics=metrics,
        async_config=AsyncConfig(run_async=True, max_concurrent=5),
        display_config=DisplayConfig(print_results=False, show_indicator=True),
        error_config=ErrorConfig(ignore_errors=True),
    )
    out: dict[str, dict[str, dict]] = {}
    for tr in result.test_results:
        out[tr.name] = {
            md.name.replace(" [GEval]", ""): {"score": md.score, "reason": md.reason or md.error, "success": md.success}
            for md in (tr.metrics_data or [])
        }
    return out


# ───────────────────────── reporting ─────────────────────────
def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.mean(xs), 4) if xs else None


def score_run(run_file: Path, judge_model: str | None) -> dict:
    run_rows = read_jsonl(run_file)
    if not run_rows:
        raise SystemExit(f"{run_file} is empty")
    golden = {g["id"]: g for g in read_jsonl(GOLDEN)} if GOLDEN.exists() else {}
    if not golden:
        print("WARNING: golden.jsonl not found - run build_golden.py; execution accuracy will be 0.")

    answer_cases, behavior_cases = build_cases(run_rows, golden)
    exec_acc, workflow = ExecutionAccuracyMetric(), WorkflowMetric()
    answer_metrics, behavior_metrics = [exec_acc, workflow], [WorkflowMetric()]
    if judge_model:
        judge = build_judge(judge_model)
        correctness, faithfulness, behavior = llm_metrics(judge)
        answer_metrics += [correctness, faithfulness]
        behavior_metrics += [behavior]

    print(f"\nScoring {run_file.name}: {len(answer_cases)} answer + {len(behavior_cases)} behaviour cases"
          f" (judge: {judge_model or 'none'})")
    scores = {**run_deepeval(answer_cases, answer_metrics), **run_deepeval(behavior_cases, behavior_metrics)}

    meta = run_rows[0]
    judge_tag = slug(judge_model) if judge_model else "none"
    per_q = []
    for r in run_rows:
        s = scores.get(r["id"], {})
        get = lambda name, key="score": (s.get(name) or {}).get(key)
        per_q.append({
            "run_id": meta["run_id"], "model": meta["model"], "config": meta["config"], "judge": judge_model or "",
            "id": r["id"], "category": r["category"], "expected_behavior": r["expected_behavior"],
            "execution_accuracy": get("Execution Accuracy"),
            "workflow": get("Workflow"),
            "answer_correctness": get("Answer Correctness"),
            "faithfulness": get("Faithfulness"),
            "behavior": get("Behavior"),
            "latency_s": r.get("latency_s"), "gb_processed": r.get("gb_processed"),
            "input_tokens": r.get("input_tokens"), "output_tokens": r.get("output_tokens"),
            "n_validate_failures": r.get("n_validate_failures"), "error": r.get("error"),
            "exec_reason": get("Execution Accuracy", "reason"),
            "workflow_reason": get("Workflow", "reason"),
            "judge_reason": get("Answer Correctness", "reason") or get("Behavior", "reason"),
            "question": r["question"], "final_sql": r.get("final_sql"),
        })

    SCORES_DIR.mkdir(parents=True, exist_ok=True)
    out = SCORES_DIR / f"{meta['run_id']}__judge-{judge_tag}.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(per_q[0].keys()))
        w.writeheader()
        w.writerows(per_q)

    ans = [q for q in per_q if q["expected_behavior"] == "answer"]
    beh = [q for q in per_q if q["expected_behavior"] != "answer"]
    lat = sorted(q["latency_s"] for q in per_q if q["latency_s"] is not None)
    summary = {
        "scored_at": dt.datetime.now().isoformat(timespec="seconds"),
        "run_id": meta["run_id"], "model": meta["model"], "config": meta["config"], "judge": judge_model or "",
        "n_questions": len(per_q),
        "execution_accuracy": _mean(q["execution_accuracy"] for q in ans),
        **{f"exec_acc_{c}": _mean(q["execution_accuracy"] for q in ans if q["category"] == c) for c in ANSWER_CATEGORIES},
        "answer_correctness": _mean(q["answer_correctness"] for q in ans),
        "faithfulness": _mean(q["faithfulness"] for q in ans),
        "behavior": _mean(q["behavior"] for q in beh),
        "behavior_no_sql_rate": _mean(q["workflow"] for q in beh),
        "workflow": _mean(q["workflow"] for q in ans),
        "avg_latency_s": round(statistics.mean(lat), 2) if lat else None,
        "p95_latency_s": lat[min(len(lat) - 1, int(0.95 * len(lat)))] if lat else None,
        "avg_gb_per_question": _mean(q["gb_processed"] for q in per_q),
        "total_input_tokens": sum(q["input_tokens"] or 0 for q in per_q),
        "total_output_tokens": sum(q["output_tokens"] or 0 for q in per_q),
        "errors": sum(1 for q in per_q if q["error"]),
        "scores_file": str(out.relative_to(LEADERBOARD.parent.parent)),
    }

    new_file = not LEADERBOARD.exists()
    with open(LEADERBOARD, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(summary.keys()))
        if new_file:
            w.writeheader()
        w.writerow(summary)

    print(f"saved -> {out}")
    for k in ("model", "config", "execution_accuracy", "answer_correctness", "faithfulness", "behavior",
              "workflow", "avg_latency_s", "avg_gb_per_question", "errors"):
        print(f"  {k:<22} {summary[k]}")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="*", help="run files to score (default: newest)")
    ap.add_argument("--all-unscored", action="store_true")
    ap.add_argument("--judge", default=None, help="Vertex Gemini judge model id (default: JUDGE_MODEL or gemini-3.8-flash)")
    ap.add_argument("--no-llm", action="store_true", help="skip LLM-judged metrics")
    args = ap.parse_args()

    load_env()
    judge = None if args.no_llm else (args.judge or os.getenv("JUDGE_MODEL", "gemini-3.8-flash"))
    runs = sorted(RUNS_DIR.glob("*.jsonl"))
    if args.runs:
        targets = [Path(p) for p in args.runs]
    elif args.all_unscored:
        scored = {p.name.split("__judge-")[0] for p in SCORES_DIR.glob("*.csv")} if SCORES_DIR.exists() else set()
        targets = [p for p in runs if p.stem not in scored]
    else:
        targets = runs[-1:]
    if not targets:
        raise SystemExit("No run files to score. Run run_agent.py first.")
    for t in targets:
        score_run(t, judge)
    print(f"\nLeaderboard: {LEADERBOARD}")


if __name__ == "__main__":
    main()
