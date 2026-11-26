import os

import pytest

from dagshub_mcp.api import DagsHubAPI, MLflowAPI, Transport
from dagshub_mcp.config import Settings


@pytest.mark.live
@pytest.mark.skipif(os.getenv("DAGSHUB_LIVE_TEST") != "1", reason="Live access is opt-in")
async def test_live_repository():
    settings = Settings.from_env()
    repo = min(settings.repositories)
    transport = Transport(settings)
    try:
        dagshub = DagsHubAPI(settings, transport)
        mlflow = MLflowAPI(settings, transport)
        info = await transport.json("GET", dagshub.base(repo))
        files = await dagshub.list_files(repo, info["default_branch"], "", 10, None)
        assert files.revision
        experiments = await mlflow.experiments(repo, 10, None)
        if experiments.items:
            runs = await mlflow.search(
                repo, [experiments.items[0]["experiment_id"]], "", [], 10, None
            )
            if runs.items:
                run = await mlflow.run(repo, runs.items[0]["run_id"])
                assert run.run_id
    finally:
        await transport.close()
