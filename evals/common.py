"""Shared helpers for the eval suite: settings, file I/O, JSON-safe values."""

from __future__ import annotations

import datetime as dt
import decimal
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent
DATASET = ROOT / "dataset.jsonl"
GOLDEN = ROOT / "golden.jsonl"            # dataset + expected results (built by build_golden.py)
RUNS_DIR = ROOT / "results" / "runs"      # one file per agent run
SCORES_DIR = ROOT / "results" / "scores"  # one file per scored run
LEADERBOARD = ROOT / "results" / "leaderboard.csv"

# Questions with relative dates ("last month") are written for this date, so runs are reproducible.
EVAL_TODAY = os.getenv("EVAL_TODAY", "2026-01-15")


def load_env() -> None:
    """Load the project's .env (bigq_mcp/.env) if python-dotenv is installed."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for candidate in (ROOT.parent / "bigq_mcp" / ".env", ROOT / ".env", Path.cwd() / ".env"):
        if candidate.exists():
            load_dotenv(candidate)
            return


def project() -> str:
    p = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("BQ_PROJECT")
    if not p:
        raise RuntimeError("Set GOOGLE_CLOUD_PROJECT (or BQ_PROJECT) in .env")
    return p


def read_jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, default=json_default, ensure_ascii=False) + "\n")


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=json_default, ensure_ascii=False) + "\n")


def json_default(v: Any) -> Any:
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, bytes):
        return v.hex()
    return str(v)


def slug(text: str) -> str:
    """Model ids -> safe file name part, e.g. 'gemini-3.8-flash' stays, 'claude-x@2025' -> 'claude-x-2025'."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-").lower()


def sql_for(golden_sql: str) -> str:
    return golden_sql.replace("{{PROJECT}}", project())
