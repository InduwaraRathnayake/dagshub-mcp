import sys

import httpx
from conftest import mock_transport, raw_run
from mcp import Client, StdioServerParameters

from dagshub_mcp.server import create_server


async def test_mcp_workflow(settings):
    sha = "a" * 40

    def handler(request):
        path = request.url.path
        if path.endswith("experiments/search"):
            return httpx.Response(200, json={"experiments": [{"experiment_id": "1"}]})
        if path.endswith("runs/search"):
            return httpx.Response(200, json={"runs": [raw_run(), raw_run("candidate", 0.9)]})
        if path.endswith("runs/get"):
            run_id = request.url.params["run_id"]
            return httpx.Response(
                200, json={"run": raw_run(run_id, 0.8 if run_id == "base" else 0.9)}
            )
        if path.endswith("/commits"):
            return httpx.Response(200, json=[{"id": sha}])
        return httpx.Response(200, content=b'{"recall":0.9}')

    transport = mock_transport(settings, handler)
    async with Client(create_server(settings, transport)) as client:
        tools = await client.list_tools()
        assert len(tools.tools) == 9
        assert all(t.output_schema for t in tools.tools)
        assert all(t.annotations.read_only_hint for t in tools.tools)
        experiments = await client.call_tool("list_experiments", {"repository": "owner/repo"})
        assert not experiments.is_error
        assert experiments.structured_content["items"][0]["experiment_id"] == "1"
        runs = await client.call_tool(
            "search_runs", {"repository": "owner/repo", "experiment_ids": ["1"]}
        )
        assert len(runs.structured_content["items"]) == 2
        result = await client.call_tool(
            "compare_runs",
            {
                "repository": "owner/repo",
                "run_ids": ["base", "candidate"],
                "baseline_run_id": "base",
                "metric": "recall",
                "direction": "maximize",
                "thresholds": [{"metric": "latency_ms", "operator": "<=", "value": 100}],
            },
        )
        assert not result.is_error
        assert result.structured_content["runs"][1]["rank"] == 1
        for tool, arguments in (
            ("read_repo_file", {"revision": "main", "path": "report.json"}),
            ("read_artifact", {"run_id": "base", "path": "report.json"}),
        ):
            result = await client.call_tool(tool, {"repository": "owner/repo", **arguments})
            assert not result.is_error
            assert result.structured_content["text"] == '{"recall":0.9}'
        denied = await client.call_tool("get_run", {"repository": "other/repo", "run_id": "base"})
        assert denied.is_error
        assert "repository_not_allowed" in denied.content[0].text
        invalid = await client.call_tool(
            "compare_runs",
            {
                "repository": "owner/repo",
                "run_ids": ["base"],
                "baseline_run_id": "base",
                "metric": "recall",
                "direction": "maximize",
            },
        )
        assert invalid.is_error
        prompts = await client.list_prompts()
        assert prompts.prompts[0].name == "analyze_experiments"
        prompt = await client.get_prompt(
            "analyze_experiments", {"repository": "owner/repo", "objective": "Recall"}
        )
        assert "Recall" in prompt.messages[0].content.text


async def test_stdio_entry_point():
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "dagshub_mcp.server"],
        env={"DAGSHUB_REPOSITORIES": "owner/repo", "DAGSHUB_USERNAME": "", "DAGSHUB_TOKEN": ""},
    )
    async with Client(parameters) as client:
        tools = await client.list_tools()
        assert len(tools.tools) == 9
        # This checks a real subprocess call without any network request.
        denied = await client.call_tool("get_run", {"repository": "denied/repo", "run_id": "base"})
        assert denied.is_error
