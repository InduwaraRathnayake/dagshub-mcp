# DagsHub MCP

Explore your DagsHub experiments with an AI agent. Compare runs, inspect evaluation
reports, and make model decisions backed by metrics and source references.

DagsHub MCP is a local, read-only [Model Context Protocol](https://modelcontextprotocol.io/)
server that connects your AI client to DagsHub repositories and their MLflow tracking servers.
Your client provides the AI model; the server retrieves evidence and calculates comparisons.

> "Compare my latest runs against the baseline for recall, require latency below 100 ms,
> and recommend the next experiment. Show the evidence and any comparability gaps."

## Features

- **Explore experiments:** search runs and inspect metrics, parameters, tags, and metric history.
- **Compare results:** calculate baseline differences, rank runs, and check metric thresholds.
- **Inspect reports:** read JSON, CSV, Markdown, and text from artifacts or repository revisions.
- **Trace the evidence:** return run references, resolved commit SHAs, and provenance warnings.
- **Use your preferred agent:** connect through local MCP over stdio, without an LLM API key in the server.

The server analyzes existing results. It does not train models, run new evaluations,
modify repositories, or deploy models.

## Quick start

### 1. Install

Requires **Python 3.11+**, [uv](https://docs.astral.sh/uv/), and access to a DagsHub repository.
Python 3.12 is the tested interpreter. For natural-language analysis, use an MCP-compatible
AI client such as VS Code with GitHub Copilot.

```sh
git clone https://github.com/InduwaraRathnayake/dagshub-mcp.git
cd dagshub-mcp
uv sync --locked --python 3.12
```

Dependencies are installed into the project's `.venv`, leaving system Python packages unchanged.

### 2. Configure DagsHub access

Create a local `.env` from the template:

```powershell
# Windows PowerShell
Copy-Item .env.example .env
```

```sh
# macOS / Linux
cp .env.example .env
```

Edit `.env`:

```dotenv
DAGSHUB_USERNAME=your-username
DAGSHUB_TOKEN=your-access-token
DAGSHUB_REPOSITORIES=owner/repository
```

| Variable | Where to find it |
| --- | --- |
| `DAGSHUB_USERNAME` | Your personal DagsHub username, including when accessing an organization repository. |
| `DAGSHUB_TOKEN` | Generate an access token in [DagsHub token settings](https://dagshub.com/user/settings/tokens). |
| `DAGSHUB_REPOSITORIES` | The `owner/repository` part of a DagsHub URL. Separate multiple entries with commas. |

Only repositories in `DAGSHUB_REPOSITORIES` can be accessed. Both credentials may be omitted
for public access, although experiment tracking can still require repository permissions.
Authentication uses your username and token through Basic authentication.

`.env` is ignored by Git. Keep tokens in your local environment and out of chat messages.

### 3. Connect your AI client

#### VS Code with GitHub Copilot

Open the cloned project folder in VS Code and sign in to GitHub Copilot. The included
[.vscode/mcp.json](.vscode/mcp.json) uses the workspace location to launch the server and load `.env`.

1. Open the Command Palette and run **MCP: List Servers**.
2. Select **dagshub**, start it, and review any server trust prompt.
3. Open Copilot Chat and select **Agent**.
4. Enable DagsHub through **Configure Tools** beside the chat input. If that picker is
   unavailable, run **Chat: Open Customizations**, select **Tools**, and enable DagsHub.
5. Ask a question using your configured repository name:

   > Use the DagsHub MCP tools to list experiments in `owner/repository`.
   > Show the latest completed runs and their available metrics.

VS Code starts its own server process. No separate terminal server or Inspector session is needed.
See the [VS Code MCP reference](https://code.visualstudio.com/docs/agents/reference/mcp-configuration)
and [tool controls](https://code.visualstudio.com/docs/agents/run/tools) for client-specific details.

#### Other local MCP clients

For clients using a `mcpServers` configuration, add this entry. Replace the directory with
an absolute path to your clone; adapt the configuration to your client's schema.

```json
{
  "mcpServers": {
    "dagshub": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/dagshub-mcp",
        "run",
        "--locked",
        "--env-file",
        ".env",
        "dagshub-mcp"
      ]
    }
  }
}
```

On Windows, a path such as `C:/Users/you/projects/dagshub-mcp` works without JSON backslash escaping.
The `--directory` argument ensures the server loads the project's environment and `.env` file.

## Example questions

Replace repository, metric, report, and run names with values from your project.

> List the experiments in `owner/repository` and summarize the latest completed runs.

> Compare these runs by `accuracy`, where higher is better. Show parameter differences
> and flag any dataset or split differences before recommending a run.

> Compare run A against baseline B for `recall`, and require `latency_ms <= 100`.
> Explain whether the improvement meets that constraint.

> Read `reports/evaluation.json` at revision `main` and inspect the selected run's
> metric history. Summarize the observations and propose the next experiment.

The server also provides an `analyze_experiments` MCP prompt for an evidence-based review.
Prompt availability and invocation depend on the client.

## Available tools

All tools require an explicit `repository` in `owner/repository` format.

| Tool | Purpose | Main inputs |
| --- | --- | --- |
| `list_experiments` | Discover active MLflow experiments | Pagination |
| `search_runs` | Find active runs using MLflow filters and sorting | `experiment_ids`, `filter_string`, `order_by`, pagination |
| `get_run` | Inspect metrics with steps/timestamps, params, tags, and dataset inputs | `run_id` |
| `get_metric_history` | Read a metric's logged history | `run_id`, `metric`, pagination |
| `list_artifacts` | Browse an artifact directory | `run_id`, `path`, pagination |
| `read_artifact` | Preview an evaluation artifact | `run_id`, `path` |
| `list_repo_files` | Browse versioned repository files | `revision`, `path`, pagination |
| `read_repo_file` | Preview a report at a resolved commit | `revision`, `path` |
| `compare_runs` | Calculate differences, ranks, and threshold results | `run_ids`, `baseline_run_id`, `metric`, `direction`, `thresholds` |

Page sizes range from 1 to 100. Follow `next_page_token` and inspect `warnings` and `truncated`.
Reuse returned commit SHAs for repository reads and subsequent pages to keep revisions consistent.

### Comparison behavior

`compare_runs` accepts 2-20 distinct runs from one repository, including the selected baseline.
Specify `maximize` or `minimize` for the objective metric. For example:

```json
{
  "repository": "owner/repository",
  "run_ids": ["baseline-run-id", "candidate-run-id"],
  "baseline_run_id": "baseline-run-id",
  "metric": "recall",
  "direction": "maximize",
  "thresholds": [
    {"metric": "latency_ms", "operator": "<=", "value": 100}
  ]
}
```

- Changes are signed `candidate - baseline`; percentage change divides by `abs(baseline)`.
  A zero baseline has no percentage change.
- Ranks use the objective metric, independently of threshold eligibility. Ties share a dense rank.
- Missing or non-finite objective/threshold values make a run unevaluable.
- Comparisons use the latest logged values, which may come from different training steps.
- Missing or differing dataset, split, and code provenance produces warnings. Summary metrics
  alone do not establish statistical significance or causation.

## Supported data and limitations

| Area | Current support |
| --- | --- |
| Transport | Local stdio MCP |
| Report formats | UTF-8 `.json`, `.csv`, `.md`, `.txt` |
| File reads | At most 1 MiB per file |
| Previews | Up to 20,000 text characters or 100 CSV rows, including a header if present |
| Repository data | Git/DVC content at a branch, tag, or commit resolved to a full SHA |
| Artifact storage | `mlflow-artifacts:/...` and DagsHub HTTPS roots scoped to the selected repository |

External S3/GCS/local artifact locations, signed URLs, redirected downloads, and Data Engine
browsing are unsupported. Large JSON previews are text excerpts rather than complete JSON objects.
No repository code or serialized model is executed. Retrieved content is treated as untrusted data.

Requests have timeouts and response-size bounds. Transient service failures and rate limits are
retried within a fixed attempt limit. Credentials are not forwarded through redirects.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Terminal appears idle | Expected: stdio waits for an MCP client. It is not an interactive chat. |
| VS Code cannot find `uv` | Make `uv` available on its PATH, or configure the executable's absolute path. |
| Server fails at startup | Check `.env`, the repository allowlist, and that both username/token are set together. |
| DagsHub tools are missing | Start the server, enable its tools in chat, and inspect **Show Output** for errors. |
| Authentication error | Check the token, personal username, and access to the selected repository. |
| Repository not allowed | Add the exact `owner/repository` to `.env`, then restart the server. |
| Empty experiment list | Confirm the repository contains MLflow experiments and your account can access them. |
| Unsupported storage or large report | Check the supported formats, artifact locations, and size limits above. |

To inspect individual tools, run this from the project root (requires Node.js/npm):

```sh
npx @modelcontextprotocol/inspector uv run --locked --env-file .env dagshub-mcp
```

Open the URL printed in the terminal, connect, and call `list_experiments` with your repository.

## Development and testing

```sh
uv sync --locked --python 3.12
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check src tests
```

The default suite uses fixtures and mocked APIs. It covers comparison calculations, report
bounds, authentication failures, pagination, retries, MCP discovery, and a real stdio subprocess.

Live tests are opt-in and read from the first configured repository in alphabetical order:

```powershell
# Windows PowerShell
$env:DAGSHUB_LIVE_TEST = "1"
uv run --locked --env-file .env pytest -m live -v
Remove-Item Env:DAGSHUB_LIVE_TEST
```

```sh
# macOS / Linux
DAGSHUB_LIVE_TEST=1 uv run --locked --env-file .env pytest -m live -v
```

The live test lists repository files and experiments, then inspects a run if available.
It does not validate every report or artifact storage configuration.

API adapters, deterministic analysis, and MCP tool registration are separated under
[`src/dagshub_mcp`](src/dagshub_mcp). Contributions should include relevant tests and pass
the checks above. When reporting an issue, include reproduction steps and sanitized error
output; omit credentials and private experiment data.

## References

- [DagsHub API documentation](https://dagshub.com/docs/api/)
- [DagsHub OpenAPI specification](https://github.com/DagsHub/openapi-description)
- [MLflow REST API](https://mlflow.org/docs/latest/api_reference/rest-api.html)
- [MCP Python SDK](https://py.sdk.modelcontextprotocol.io/)
