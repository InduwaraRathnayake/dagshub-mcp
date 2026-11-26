"""Deterministic comparisons. This module has no network or LLM dependency."""

import operator
from typing import Literal

from .config import DagsHubError
from .models import ComparedRun, Comparison, Run, Threshold, ThresholdResult

OPS = {">=": operator.ge, "<=": operator.le, ">": operator.gt, "<": operator.lt}


def provenance(run: Run) -> dict:
    return {
        "code_version": run.tags.get("mlflow.source.git.commit") or run.tags.get("git.commit"),
        "dataset": run.tags.get("dataset") or run.params.get("dataset"),
        "split": run.tags.get("split") or run.params.get("split"),
        "dataset_inputs": run.datasets,
    }


def compare(
    runs: list[Run],
    baseline: str,
    metric: str,
    direction: Literal["maximize", "minimize"],
    thresholds: list[Threshold],
) -> Comparison:
    if not 2 <= len(runs) <= 20 or len({r.run_id for r in runs}) != len(runs):
        raise DagsHubError("invalid_argument", "Select 2–20 distinct runs.")
    if len({r.repository for r in runs}) != 1:
        raise DagsHubError("invalid_argument", "Runs must belong to the same repository.")
    if direction not in {"maximize", "minimize"} or not metric.strip():
        raise DagsHubError("invalid_argument", "Specify a metric and maximize/minimize direction.")
    base = next((r for r in runs if r.run_id == baseline), None)
    if base is None:
        raise DagsHubError("invalid_argument", "Baseline must be among selected run IDs.")
    base_metric = base.metrics.get(metric)
    base_value = base_metric.value if base_metric else None
    warnings = list(dict.fromkeys(w for r in runs for w in r.warnings))
    warnings.append(
        "Latest logged values are compared; steps may differ. No significance test is performed."
    )
    rows = []
    for run in runs:
        m = run.metrics.get(metric)
        value = m.value if m else None
        checks = []
        for threshold in thresholds:
            actual = run.metrics.get(threshold.metric)
            actual_value = actual.value if actual else None
            checks.append(
                ThresholdResult(
                    threshold=threshold,
                    actual=actual_value,
                    passed=None
                    if actual_value is None
                    else OPS[threshold.operator](actual_value, threshold.value),
                )
            )
        eligibility = (
            "unevaluable"
            if value is None or any(c.passed is None for c in checks)
            else "fail"
            if any(c.passed is False for c in checks)
            else "pass"
        )
        delta = value - base_value if value is not None and base_value is not None else None
        rows.append(
            ComparedRun(
                run_id=run.run_id,
                source=run.source,
                value=value,
                step=m.step if m else None,
                absolute_change=delta,
                percentage_change=100 * delta / abs(base_value)
                if delta is not None and base_value
                else None,
                thresholds=checks,
                eligibility=eligibility,
                provenance=provenance(run),
            )
        )
        if value is None:
            warnings.append(f"Run {run.run_id} has no finite value for {metric}.")
        if run.status != "FINISHED":
            warnings.append(f"Run {run.run_id} has status {run.status}; results may be incomplete.")
    values = sorted({r.value for r in rows if r.value is not None}, reverse=direction == "maximize")
    for row in rows:
        if row.value is not None:
            row.rank = values.index(row.value) + 1
    for key in ("code_version", "dataset", "split", "dataset_inputs"):
        known = [r.provenance[key] for r in rows if r.provenance[key]]
        if len(known) != len(rows):
            warnings.append(f"Missing {key} provenance; comparability is not established.")
        if any(v != known[0] for v in known[1:]):
            warnings.append(f"Selected runs differ in {key} provenance.")
    if base_value is None:
        warnings.append("Baseline metric is missing; changes cannot be calculated.")
    elif base_value == 0:
        warnings.append("Baseline is zero; percentage changes are undefined.")
    return Comparison(
        repository=base.repository,
        source=base.source,
        warnings=warnings,
        baseline_run_id=baseline,
        metric=metric,
        direction=direction,
        runs=rows,
    )
