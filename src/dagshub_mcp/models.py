"""Typed output contracts shared by tools and analysis."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class Result(BaseModel):
    repository: str
    source: str
    warnings: list[str] = Field(default_factory=list)
    truncated: bool = False


class Page(Result):
    items: list[dict[str, Any]] = Field(default_factory=list)
    next_page_token: str | None = None
    revision: str | None = None


class Metric(BaseModel):
    value: float | None
    step: int = 0
    timestamp: int = 0


class Run(Result):
    run_id: str
    experiment_id: str
    status: str
    artifact_uri: str = ""
    metrics: dict[str, Metric] = Field(default_factory=dict)
    params: dict[str, str] = Field(default_factory=dict)
    tags: dict[str, str] = Field(default_factory=dict)
    datasets: list[dict[str, Any]] = Field(default_factory=list)


class FilePreview(Result):
    path: str
    revision: str | None = None
    run_id: str | None = None
    format: str
    bytes_read: int
    text: str
    rows: list[list[str]] | None = None


class Threshold(BaseModel):
    metric: str = Field(min_length=1)
    operator: Literal[">=", "<=", ">", "<"]
    value: float = Field(allow_inf_nan=False)


class ThresholdResult(BaseModel):
    threshold: Threshold
    actual: float | None
    passed: bool | None


class ComparedRun(BaseModel):
    run_id: str
    source: str
    value: float | None
    step: int | None
    rank: int | None = None
    absolute_change: float | None = None
    percentage_change: float | None = None
    thresholds: list[ThresholdResult]
    eligibility: Literal["pass", "fail", "unevaluable"]
    provenance: dict[str, Any]


class Comparison(Result):
    baseline_run_id: str
    metric: str
    direction: Literal["maximize", "minimize"]
    runs: list[ComparedRun]
