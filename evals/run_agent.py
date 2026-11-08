"""Run the agent on the eval questions and save one trace per question.

Every run file is named   results/runs/<timestamp>__<model>__<config>.jsonl
and every record inside has "model", so results from different models never mix.

    uv run evals/run_agent.py --model gemini-3.8-flash
    uv run evals/run_agent.py --model gemini-3.8-flash gemini-3.5-flash-lite --config rag no_rag
    uv run evals/run_agent.py --model gemini-3.8-flash --ids e01 m06 a01

Start the MCP server first (AUTH_MODE=none is simplest for unattended evals).
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import time
from typing import Any

from common import EVAL_TODAY, GOLDEN, RUNS_DIR, DATASET, append_jsonl, load_env, read_jsonl, slug
from agent_factory import CONFIGS, build_agent, build_llm, build_system_prompt, open_tools


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content or [])


def _json(content: Any) -> dict:
    try:
        return json.loads(_text(content))
    except (ValueError, TypeError):
        return {}


def summarise_trace(messages: list) -> dict:
    """Pull tool calls, the final SQL/result and token counts out of the agent's messages."""
    calls, results = [], {}
    in_tok = out_tok = 0
    for m in messages:
        usage = getattr(m, "usage_metadata", None) or {}
        in_tok += usage.get("input_tokens", 0) or 0
        out_tok += usage.get("output_tokens", 0) or 0
        for c in getattr(m, "tool_calls", None) or []:
            calls.append({"id": c.get("id"), "name": c["name"], "args": c.get("args", {})})
        if getattr(m, "type", "") == "tool":
            results[getattr(m, "tool_call_id", None)] = (getattr(m, "name", None), _json(m.content))

    for c in calls:
        name, out = results.get(c["id"], (c["name"], {}))
        c["ok"] = bool(out.get("ok"))
        c["error"] = out.get("error")

    last_run = None
    for c in calls:
        if c["name"] == "run_sql" and c["ok"]:
            last_run = results.get(c["id"], (None, {}))[1]

    return {
        "tool_calls": [{k: v for k, v in c.items() if k != "id"} for c in calls],
        "tool_sequence": [c["name"] for c in calls],
        "n_validate_failures": sum(1 for c in calls if c["name"] == "validate_sql" and not c["ok"]),
        "n_run_calls": sum(1 for c in calls if c["name"] == "run_sql"),
        "final_sql": (last_run or {}).get("sql"),
        "result_rows": (last_run or {}).get("rows") if last_run else None,
        "delivery": (last_run or {}).get("delivery"),
        "total_rows": (last_run or {}).get("total_rows"),
        "gb_processed": sum((results.get(c["id"], (None, {}))[1].get("gb_processed") or 0)
                            for c in calls if c["name"] == "run_sql"),
        "input_tokens": in_tok,
        "output_tokens": out_tok,
    }


async def run_one(agent, item: dict, meta: dict, timeout_s: float) -> dict:
    config = {"configurable": {"thread_id": f"{meta['run_id']}:{item['id']}"}, "recursion_limit": 40}
    t0 = time.perf_counter()
    record = {**meta, "id": item["id"], "category": item["category"], "question": item["question"],
              "expected_behavior": item["expected_behavior"]}
    try:
        result = await asyncio.wait_for(
            agent.ainvoke({"messages": [{"role": "user", "content": item["question"]}]}, config), timeout_s)
        msgs = result["messages"]
        record.update(answer=_text(msgs[-1].content), error=None, **summarise_trace(msgs))
    except Exception as e:  # keep going; the failure is scored as a miss
        record.update(answer="", error=f"{type(e).__name__}: {e}", tool_calls=[], tool_sequence=[],
                      result_rows=None, final_sql=None)
    record["latency_s"] = round(time.perf_counter() - t0, 2)
    return record


async def run(model: str, config_name: str, items: list[dict], mcp_url, oauth: bool, timeout_s: float,
              concurrency: int = 1) -> str:
    """Run one model x config. `concurrency` questions run at the same time (each in its own thread_id)."""
    cfg = CONFIGS[config_name]
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_id = f"{stamp}__{slug(model)}__{config_name}"
    out = RUNS_DIR / f"{run_id}.jsonl"
    tag = f"{model} [{config_name}]"

    elicitations: list[dict] = []
    adapter, tools = await open_tools(mcp_url, cfg["use_examples"], elicitations, oauth=oauth)
    try:
        agent = build_agent(build_llm(model), tools, build_system_prompt(EVAL_TODAY, cfg["use_examples"]))
        meta = {"run_id": run_id, "model": model, "config": config_name, "eval_today": EVAL_TODAY,
                "tools": sorted(t.name for t in tools)}
        print(f"=== {run_id}  ({len(items)} questions, {concurrency} at a time) ===")
        sem = asyncio.Semaphore(max(1, concurrency))
        done = 0
        lock = asyncio.Lock()

        async def one(item: dict):
            nonlocal done
            async with sem:
                rec = await run_one(agent, item, meta, timeout_s)
            async with lock:  # one writer at a time; a crash keeps finished questions
                append_jsonl(out, rec)
                done += 1
                status = "ERR " if rec["error"] else ("SQL " if rec.get("final_sql") else "TEXT")
                print(f"{tag[:38]:<38} [{done:02d}/{len(items)}] {item['id']:>4} {status} {rec['latency_s']:>6.1f}s  "
                      f"{' > '.join(rec.get('tool_sequence', []))[:70]}")

        await asyncio.gather(*(one(i) for i in items))
    finally:
        await adapter.__aexit__(None, None, None)
    print(f"saved -> {out}")
    return str(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", nargs="+", required=True, help="one or more Vertex AI model ids")
    ap.add_argument("--config", nargs="+", default=["rag"], choices=list(CONFIGS))
    ap.add_argument("--ids", nargs="*", help="only these question ids")
    ap.add_argument("--categories", nargs="*", help="only these categories (easy, medium, hard, ...)")
    ap.add_argument("--limit", type=int, help="first N questions only")
    ap.add_argument("--mcp-url", default=None)
    ap.add_argument("--oauth", action="store_true", help="use Google OAuth (server AUTH_MODE=google)")
    ap.add_argument("--timeout", type=float, default=180, help="seconds per question")
    ap.add_argument("--concurrency", type=int, default=1, help="questions running at the same time (1 = one by one)")
    args = ap.parse_args()

    load_env()
    items = read_jsonl(GOLDEN if GOLDEN.exists() else DATASET)
    if args.ids:
        items = [i for i in items if i["id"] in args.ids]
    if args.categories:
        items = [i for i in items if i["category"] in args.categories]
    if args.limit:
        items = items[: args.limit]
    mcp_url = args.mcp_url or os.getenv("MCP_URL", "http://localhost:8000/mcp")

    for model in args.model:
        for config_name in args.config:
            asyncio.run(run(model, config_name, items, mcp_url, args.oauth, args.timeout, args.concurrency))


if __name__ == "__main__":
    main()