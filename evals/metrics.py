"""Deterministic metrics (no LLM judge): execution accuracy and tool workflow."""

from __future__ import annotations

import math
from typing import Any

from deepeval.metrics import BaseMetric
from deepeval.test_case import LLMTestCase


# ───────────────────────── result comparison ─────────────────────────
def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _close(a: float, g: float) -> bool:
    """Equal within rounding: agents often round to 1-2 decimals."""
    return math.isclose(a, g, rel_tol=5e-3, abs_tol=0.051)


def _column_matches(agent_vals: list[float], golden_vals: list[float]) -> bool:
    a, g = sorted(agent_vals), sorted(golden_vals)
    for scale in (1.0, 100.0, 0.01):  # also accept ratio vs percent (0.123 vs 12.3)
        if all(_close(x * scale, y) for x, y in zip(a, g)):
            return True
    return False


def compare_results(expected: list[dict] | None, actual: list[dict] | None,
                    value_columns: list[str]) -> tuple[float, str]:
    """1.0 if the agent's result has the same number of rows and, for every value column of the golden
    result, some numeric column with the same values (any name, any row order, within rounding).

    Key columns (month, zone, borough...) are not compared directly: agents name and format them in many
    valid ways (7 vs 'July' vs '2025-07-01'). Matching every measure column plus the row count makes a
    false pass very unlikely."""
    if expected is None:
        return 1.0, "no golden result for this question"
    if not actual:
        return 0.0, "agent produced no successful query result"
    if len(actual) != len(expected):
        return 0.0, f"row count {len(actual)} != expected {len(expected)}"

    agent_cols: dict[str, list[float]] = {}
    for col in actual[0].keys():
        vals = [_num(r.get(col)) for r in actual]
        if all(v is not None for v in vals):
            agent_cols[col] = vals  # numeric column

    missing = []
    for col in value_columns:
        golden_vals = [_num(r.get(col)) for r in expected]
        if any(v is None for v in golden_vals):
            continue  # NULLs in golden (e.g. first LAG row) - skip strict check
        if not any(_column_matches(v, golden_vals) for v in agent_cols.values()):
            missing.append(col)
    if missing:
        return 0.0, f"no agent column matches expected values for {missing}"
    return 1.0, f"{len(expected)} rows; values match for {value_columns}"


# ───────────────────────── DeepEval metrics ─────────────────────────
class _Deterministic(BaseMetric):
    def __init__(self, threshold: float = 1.0):
        self.threshold = threshold
        self.async_mode = False
        self.include_reason = True
        self.strict_mode = False
        self.evaluation_model = "deterministic"

    async def a_measure(self, test_case: LLMTestCase, *args, **kwargs) -> float:
        return self.measure(test_case)

    def is_successful(self) -> bool:
        return bool(self.success)


class ExecutionAccuracyMetric(_Deterministic):
    """Did the agent's query return the same numbers as the golden query?"""

    @property
    def __name__(self):
        return "Execution Accuracy"

    def measure(self, test_case: LLMTestCase, *args, **kwargs) -> float:
        md = test_case.metadata or {}
        self.score, self.reason = compare_results(md.get("expected_rows"), md.get("result_rows"),
                                                  md.get("value_columns") or [])
        self.success = self.score >= self.threshold
        return self.score


class WorkflowMetric(_Deterministic):
    """Did the agent follow the loop? Fraction of checks passed.

    Answer questions: (rag) searched examples first; validated before running; ran a query;
    <= 3 failed validations; never tried save_example.
    Behaviour questions (clarify / unanswerable / decline): ran no SQL."""

    def __init__(self, threshold: float = 0.75):
        super().__init__(threshold)

    @property
    def __name__(self):
        return "Workflow"

    def measure(self, test_case: LLMTestCase, *args, **kwargs) -> float:
        md = test_case.metadata or {}
        seq = md.get("tool_sequence") or []
        checks: dict[str, bool] = {}
        if md.get("expected_behavior") == "answer":
            if "search_examples" in (md.get("tools") or []):
                checks["searched_examples_first"] = bool(seq) and seq[0] == "search_examples"
            first_run = seq.index("run_sql") if "run_sql" in seq else None
            checks["ran_query"] = first_run is not None
            checks["validated_before_run"] = first_run is not None and "validate_sql" in seq[:first_run]
            checks["retries_within_3"] = (md.get("n_validate_failures") or 0) <= 3
            checks["no_save_attempt"] = "save_example" not in seq
        else:
            checks["no_sql_run"] = "run_sql" not in seq
        passed = [k for k, v in checks.items() if v]
        self.score = len(passed) / len(checks)
        failed = [k for k, v in checks.items() if not v]
        self.reason = "all checks passed" if not failed else f"failed: {', '.join(failed)}"
        self.success = self.score >= self.threshold
        return self.score
