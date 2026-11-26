# DagsHub MCP

A local, read-only MCP server for comparing MLflow runs and inspecting result files in DagsHub.
Your MCP client supplies the AI model; this server retrieves evidence and calculates comparisons.

## Install in a virtual environment

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/). All dependencies are installed into
this project's `.venv`, not the system Python. Python 3.12 is the tested interpreter.

```powershell
uv sync --locked --python 3.12
Copy-Item .env.example .env
# Edit .env with your own repository allowlist and credentials.
uv run --locked --env-file .env dagshub-mcp
```

The server waits for MCP on stdin/stdout; an idle terminal is expected. Logs go to stderr.
`.env` is ignored and only loaded when explicitly passed to uv. Never paste tokens into a chat.
For public repositories, both username and token may be omitted; MLflow access can still require
repository permissions. Credentialed access uses username/token Basic authentication.

| Variable | Value |
| --- | --- |
| `DAGSHUB_USERNAME` | Your personal DagsHub username, including when accessing an organization repository. |
| `DAGSHUB_TOKEN` | An access token from [DagsHub token settings](https://dagshub.com/user/settings/tokens). |
| `DAGSHUB_REPOSITORIES` | Comma-separated `owner/repository` names taken from your DagsHub repository URLs. |

## Ask questions in VS Code

The included [.vscode/mcp.json](.vscode/mcp.json) connects this server to VS Code.
Open this repository as the workspace folder, complete the virtual environment and `.env`
setup above, and sign in to GitHub Copilot.

1. Run **MCP: List Servers** from the Command Palette (`Ctrl+Shift+P`), select `dagshub`,
   and start it. Review the server trust prompt if shown.
2. Open Copilot Chat and select **Agent**.
3. Enable the DagsHub tools through **Configure Tools** beside the chat input. If your
   interface has no tool picker, run **Chat: Open Customizations**, select **Tools**,
   and enable the DagsHub group.
4. Ask: "Use the DagsHub MCP tools to list experiments in `owner/repository`, then show
   the latest completed runs and their available metrics."
5. Follow up: "Compare those runs by `accuracy`, where higher is better. Show run IDs,
   metric values, parameter differences, and any dataset or split differences."

Replace the example repository and metric names with your own. Tool calls should appear in
the chat before the agent explains the results. Copilot supplies the AI model; the MCP
server needs no additional LLM API key. VS Code starts its own server process, so you do
not need a separate terminal server or Inspector session running.

If DagsHub is missing or fails to start, select **Show Output** for the server and check
that `uv` is available to VS Code and that `.env` exists in the workspace root.
See [VS Code MCP configuration](https://code.visualstudio.com/docs/agents/reference/mcp-configuration)
and [tool availability](https://code.visualstudio.com/docs/agents/run/tools) for client details.

### Sharing the configuration

Commit `.vscode/mcp.json`: it uses `${workspaceFolder}` and contains no credentials or
machine-specific paths. Each contributor creates their own ignored `.env` and local `.venv`.
Keep real tokens out of `mcp.json`, `.env.example`, and Git history. Commit `.env.example`
with placeholders and `uv.lock` so others can reproduce the setup.

## Connect any local MCP client

Merge this server entry into the configuration format your client supports. Replace the directory
with this project's absolute path. The `--directory` option ensures `.env` resolves correctly.

```json
{
  "mcpServers": {
    "dagshub": {
      "command": "uv",
      "args": ["--directory", "C:/absolute/path/dagshub_mcp", "run", "--locked",
               "--env-file", ".env", "dagshub-mcp"]
    }
  }
}
```

For an interactive protocol inspection (requires Node.js/npm):

```powershell
npx @modelcontextprotocol/inspector uv --directory "C:/absolute/path/dagshub_mcp" run --locked --env-file .env dagshub-mcp
```

## Tools

| Tool | Inputs beyond `repository` | Result |
| --- | --- | --- |
| `list_experiments` | `limit`, `page_token` | Experiment page |
| `search_runs` | `experiment_ids`, optional MLflow `filter_string`, `order_by`, pagination | Run page |
| `get_run` | `run_id` | Latest metrics with step/timestamp, params, tags, dataset inputs |
| `get_metric_history` | `run_id`, `metric`, pagination | Metric-history page |
| `list_artifacts` | `run_id`, optional directory `path`, pagination | Artifact entries |
| `read_artifact` | `run_id`, `path` | Bounded report preview |
| `list_repo_files` | `revision`, optional directory `path`, pagination | File entries and resolved commit |
| `read_repo_file` | `revision`, `path` | Report preview pinned to a resolved commit |
| `compare_runs` | `run_ids`, `baseline_run_id`, `metric`, `direction`, optional `thresholds` | Differences, ranks, eligibility, provenance warnings |

Every repository must be listed in `DAGSHUB_REPOSITORIES` (comma-separated `owner/repo` names).
Page limits are 1–100. Follow `next_page_token`; inspect `truncated` and `warnings`.
Repository listings use local pagination over one bounded upstream directory response. Reuse
the returned commit SHA for subsequent pages so branch changes do not mix revisions.

Example `compare_runs` arguments (replace the run IDs):

```json
{
  "repository": "owner/repository",
  "run_ids": ["baseline-run-id", "candidate-run-id"],
  "baseline_run_id": "baseline-run-id",
  "metric": "recall",
  "direction": "maximize",
  "thresholds": [{"metric": "latency_ms", "operator": "<=", "value": 100}]
}
```

Changes are signed `candidate - baseline`, with percentage change divided by `abs(baseline)`.
Zero baselines have no percentage change. Ranks use only the objective metric; eligibility is
reported separately. Ties share a dense rank. Missing/non-finite values remain missing;
missing objective/threshold metrics make a run unevaluable. Latest logged metrics can come from
different training steps. The server reports provenance differences and gaps rather than assuming
all runs used equivalent data. No significance or causal claims are calculated.

The `analyze_experiments` MCP prompt guides the agent through evidence gathering and recommendations.
Try: "Compare my latest runs against the baseline for recall, require latency <= 100 ms,
inspect reports/evaluation.json, and recommend a next experiment with supporting evidence."

## File and storage support

UTF-8 `.json`, `.csv`, `.md`, and `.txt` files only. Reads stop at 1 MiB; previews cap text at
20,000 characters and CSV at 100 rows (including a header, if present). Large JSON previews are
text excerpts, not complete JSON objects. Files, tags, and parameters are untrusted data.

Artifact reads support `mlflow-artifacts:/...` and DagsHub-hosted HTTPS roots scoped to the
selected repository. External S3/GCS/local artifacts, signed URLs, and redirected downloads return
explicit errors. No executable reports or serialized models are loaded. Repository content APIs
cover Git/DVC files; this version does not browse Data Engine or arbitrary external buckets.

HTTP requests use timeouts and bounded responses, retry transient service failures/rate limits,
and do not forward credentials through redirects. API response bodies and credentials are not
included in error messages. The server exposes no generic HTTP, shell, write, or deployment tool.

## Verification

```powershell
uv run --locked pytest
uv run --locked ruff check .
```

Offline tests cover comparisons, adapters, report bounds, authentication failures, retries,
and MCP discovery/calls. An SDK stdio test exercises the actual process entry point.
For an opt-in live read of one configured repository:

```powershell
$env:DAGSHUB_LIVE_TEST = "1"
uv run --locked --env-file .env pytest -m live
```

The live test resolves the default branch, lists files and experiments, and inspects a run if
available. It never writes. Real report/artifact validation additionally requires representative
paths/run IDs from your repository; offline fixtures cannot establish live storage compatibility.

Implementation references: [DagsHub API](https://dagshub.com/docs/api/),
[DagsHub OpenAPI](https://github.com/DagsHub/openapi-description),
[MLflow REST](https://mlflow.org/docs/latest/api_reference/rest-api.html),
[MCP Python SDK](https://py.sdk.modelcontextprotocol.io/).
