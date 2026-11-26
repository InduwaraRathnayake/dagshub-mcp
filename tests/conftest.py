import httpx
import pytest

from dagshub_mcp.api import MLflowAPI, Transport
from dagshub_mcp.config import Settings


@pytest.fixture
def settings():
    return Settings(frozenset({"owner/repo"}), "test-user", "test-secret")


def raw_run(
    run_id="base", value=0.8, latency=90, artifact_uri="mlflow-artifacts:/1/base/artifacts"
):
    metrics = (
        [] if value is None else [{"key": "recall", "value": value, "step": 5, "timestamp": 100}]
    )
    if latency is not None:
        metrics.append({"key": "latency_ms", "value": latency})
    return {
        "info": {
            "run_id": run_id,
            "experiment_id": "1",
            "status": "FINISHED",
            "artifact_uri": artifact_uri,
        },
        "data": {
            "metrics": metrics,
            "params": [{"key": "dataset", "value": "test-v1"}],
            "tags": [
                {"key": "split", "value": "test"},
                {"key": "mlflow.source.git.commit", "value": "abc"},
            ],
        },
    }


@pytest.fixture
def run_factory(settings):
    def make(run_id="base", value=0.8, latency=90):
        api = MLflowAPI(settings, None)
        return api.normalize("owner/repo", raw_run(run_id, value, latency))

    return make


def mock_transport(settings, handler):
    return Transport(settings, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
