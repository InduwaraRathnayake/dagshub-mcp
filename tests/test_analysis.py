import pytest

from dagshub_mcp.analysis import compare
from dagshub_mcp.config import DagsHubError
from dagshub_mcp.models import Threshold


def test_comparison_arithmetic_and_thresholds(run_factory):
    runs = [run_factory(), run_factory("better", 0.9, 110)]
    result = compare(
        runs,
        "base",
        "recall",
        "maximize",
        [Threshold(metric="latency_ms", operator="<=", value=100)],
    )
    candidate = result.runs[1]
    assert candidate.rank == 1
    assert candidate.absolute_change == pytest.approx(0.1)
    assert candidate.percentage_change == pytest.approx(12.5)
    assert candidate.eligibility == "fail"
    assert result.runs[0].eligibility == "pass"


def test_minimize_ties_and_negative_baseline(run_factory):
    runs = [run_factory(value=-2), run_factory("a", -1), run_factory("b", -1)]
    result = compare(runs, "base", "recall", "minimize", [])
    assert [r.rank for r in result.runs] == [1, 2, 2]
    assert result.runs[1].percentage_change == 50


def test_zero_missing_and_provenance(run_factory):
    runs = [run_factory(value=0), run_factory("missing", None, None)]
    runs[1].params["dataset"] = "another"
    runs[1].tags.clear()
    result = compare(
        runs,
        "base",
        "recall",
        "maximize",
        [Threshold(metric="latency_ms", operator="<=", value=100)],
    )
    assert result.runs[0].percentage_change is None
    assert result.runs[1].rank is None
    assert result.runs[1].eligibility == "unevaluable"
    assert result.runs[1].thresholds[0].passed is None
    assert any("differ in dataset" in w for w in result.warnings)
    assert any("Missing split" in w for w in result.warnings)
    assert any("Baseline is zero" in w for w in result.warnings)


def test_missing_baseline_and_nonfinite(run_factory):
    runs = [run_factory(value=float("nan")), run_factory("a", 0.9)]
    result = compare(runs, "base", "recall", "maximize", [])
    assert result.runs[1].absolute_change is None
    assert any("non-finite" in w for w in result.warnings)


@pytest.mark.parametrize("ids,baseline", [(["a"], "a"), (["a", "a"], "a"), (["a", "b"], "c")])
def test_invalid_selection(run_factory, ids, baseline):
    with pytest.raises(DagsHubError):
        compare([run_factory(i) for i in ids], baseline, "recall", "maximize", [])
