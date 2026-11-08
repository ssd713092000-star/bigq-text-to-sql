"""Run every golden SQL once and save the expected results to golden.jsonl.

Expected answers come from running the golden SQL, never from hand-typed numbers.
Re-run this whenever the silver table is rebuilt.

    uv run evals/build_golden.py              # all questions
    uv run evals/build_golden.py --ids e01 h04
"""

from __future__ import annotations

import argparse
import sys
import time

from common import DATASET, GOLDEN, json_default, load_env, project, read_jsonl, sql_for, write_jsonl


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", nargs="*", help="only these question ids")
    args = ap.parse_args()

    load_env()
    from google.cloud import bigquery  # uses your gcloud login (ADC)

    client = bigquery.Client(project=project())
    dataset = read_jsonl(DATASET)
    existing = {r["id"]: r for r in read_jsonl(GOLDEN)} if GOLDEN.exists() else {}

    out, total_gb, failed = [], 0.0, []
    for item in dataset:
        if args.ids and item["id"] not in args.ids:
            out.append(existing.get(item["id"], item))
            continue
        if not item["golden_sql"]:
            out.append({**item, "expected_rows": None})
            continue
        t0 = time.perf_counter()
        try:
            job = client.query(sql_for(item["golden_sql"]))
            rows = [{k: json_default(v) if not isinstance(v, (int, float, str, type(None))) else v
                     for k, v in dict(r.items()).items()} for r in job.result()]
        except Exception as e:  # keep going; report at the end
            failed.append((item["id"], getattr(e, "message", None) or str(e)))
            out.append(existing.get(item["id"], {**item, "expected_rows": None}))
            print(f"{item['id']}: FAILED - {failed[-1][1][:150]}")
            continue
        gb = (job.total_bytes_processed or 0) / 1024**3
        total_gb += gb
        if len(rows) > 500:
            print(f"WARNING {item['id']}: {len(rows)} rows (> 500) - the agent will get a GCS link, "
                  "so execution accuracy can't be checked. Make the question more aggregated.", file=sys.stderr)
        out.append({**item, "expected_rows": rows})
        print(f"{item['id']}: {len(rows)} rows, {gb:.3f} GB, {time.perf_counter() - t0:.1f}s")

    write_jsonl(GOLDEN, out)
    print(f"\nWrote {GOLDEN} ({len(out)} questions), scanned {total_gb:.2f} GB total")
    if failed:
        print(f"\n{len(failed)} golden queries failed - fix them in dataset.jsonl and re-run with --ids "
              + " ".join(i for i, _ in failed))
        sys.exit(1)


if __name__ == "__main__":
    main()