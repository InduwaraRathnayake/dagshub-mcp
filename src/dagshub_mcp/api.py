"""Read-only DagsHub and MLflow REST adapters with bounded transport."""

import asyncio
import json
import math
import re
from urllib.parse import quote, unquote, urlsplit

import httpx

from .config import DagsHubError, Settings, relative_path, segment
from .files import MAX_BYTES, preview, report_format
from .models import Metric, Page, Run

HOST = "https://dagshub.com"


def q(value: str) -> str:
    return quote(value, safe="")


def qp(value: str) -> str:
    return "/".join(q(part) for part in value.split("/"))


def limit_check(limit: int) -> None:
    if not 1 <= limit <= 100:
        raise DagsHubError("invalid_argument", "limit must be between 1 and 100.")


def read_cursor(token: str | None, context: list[str]) -> tuple[str | None, int]:
    """Keep local preview offsets separate from opaque upstream pagination tokens."""
    if not token:
        return None, 0
    try:
        cursor = json.loads(token)
        if (
            cursor["context"] != context
            or type(cursor["offset"]) is not int
            or cursor["offset"] < 0
            or not isinstance(cursor["upstream"], (str, type(None)))
        ):
            raise ValueError()
        return cursor["upstream"], cursor["offset"]
    except (ValueError, TypeError, KeyError):
        raise DagsHubError("invalid_argument", "Invalid page token for this request.") from None


def next_cursor(
    context: list[str],
    upstream: str | None,
    offset: int,
    count: int,
    limit: int,
    next_upstream: str | None,
) -> str | None:
    if offset + limit < count:
        return json.dumps({"context": context, "upstream": upstream, "offset": offset + limit})
    if next_upstream:
        return json.dumps({"context": context, "upstream": next_upstream, "offset": 0})
    return None


class Transport:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.client = client or httpx.AsyncClient(
            timeout=30, follow_redirects=False, trust_env=False
        )
        self.auth = httpx.BasicAuth(settings.username, settings.token) if settings.token else None

    async def close(self) -> None:
        await self.client.aclose()

    async def read(
        self, method: str, url: str, *, params=None, body=None, max_bytes: int = MAX_BYTES
    ) -> bytes:
        if urlsplit(url).netloc != "dagshub.com" or not url.startswith(HOST + "/"):
            raise DagsHubError("unsupported_storage", "Only DagsHub-hosted URLs are supported.")
        # POST is used only for the two read-only MLflow search endpoints.
        if method != "GET" and not (
            method == "POST" and url.endswith(("/mlflow/experiments/search", "/mlflow/runs/search"))
        ):
            raise DagsHubError("read_only", "This server only performs read operations.")
        for attempt in range(3):
            try:
                async with self.client.stream(
                    method, url, params=params, json=body, auth=self.auth, follow_redirects=False
                ) as response:
                    status = response.status_code
                    if status == 429 or status in {502, 503, 504}:
                        if attempt < 2:
                            retry_after = response.headers.get("Retry-After", "")
                            delay = (
                                min(float(retry_after), 5)
                                if retry_after.isdigit()
                                else 0.25 * 2**attempt
                            )
                            await asyncio.sleep(delay)
                            continue
                        code = "rate_limited" if status == 429 else "service_unavailable"
                        raise DagsHubError(code, "DagsHub is busy; retry the tool later.")
                    if status in {401, 403}:
                        raise DagsHubError(
                            "authentication", "Check credentials and repository access."
                        )
                    if status == 404:
                        raise DagsHubError(
                            "not_found", "Repository, run, revision, file, or endpoint not found."
                        )
                    if 300 <= status < 400:
                        raise DagsHubError(
                            "unsupported_redirect", "Redirected downloads are not supported."
                        )
                    if status >= 400:
                        raise DagsHubError("upstream_error", f"DagsHub returned HTTP {status}.")
                    declared = response.headers.get("Content-Length", "")
                    if declared.isdigit() and int(declared) > max_bytes:
                        raise DagsHubError(
                            "response_too_large", "Response exceeds the configured read limit."
                        )
                    chunks = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=16_384):
                        chunks.extend(chunk)
                        if len(chunks) > max_bytes:
                            raise DagsHubError(
                                "response_too_large", "Response exceeds the configured read limit."
                            )
                    return bytes(chunks)
            except httpx.TimeoutException:
                if attempt == 2:
                    raise DagsHubError(
                        "timeout", "DagsHub request timed out; retry later."
                    ) from None
            except httpx.RequestError:
                raise DagsHubError("connection", "Unable to connect to DagsHub.") from None
        raise AssertionError("unreachable")

    async def json(self, method: str, url: str, **kwargs):
        data = await self.read(method, url, max_bytes=4 * MAX_BYTES, **kwargs)
        try:
            return json.loads(data)
        except (ValueError, RecursionError):
            raise DagsHubError("invalid_response", "DagsHub returned invalid JSON.") from None


class DagsHubAPI:
    def __init__(self, settings: Settings, transport: Transport):
        self.settings = settings
        self.http = transport

    def base(self, repo: str) -> str:
        return f"{HOST}/api/v1/repos/{self.settings.check_repo(repo)}"

    async def revision(self, repo: str, ref: str) -> str:
        if not ref or len(ref) > 256 or any(ord(c) < 32 for c in ref):
            raise DagsHubError("invalid_argument", "Provide a branch, tag, or commit SHA.")
        commits = await self.http.json(
            "GET", self.base(repo) + "/commits", params={"sha": ref, "limit": 1}
        )
        if not isinstance(commits, list) or not commits:
            raise DagsHubError("invalid_response", "Could not resolve revision to a commit.")
        sha = commits[0].get("id") or commits[0].get("sha")
        if not isinstance(sha, str) or not re.fullmatch(r"[a-fA-F0-9]{40,64}", sha):
            raise DagsHubError(
                "invalid_response", "Revision lookup did not return a full commit SHA."
            )
        return sha

    async def list_files(
        self, repo: str, ref: str, path: str, limit: int, page_token: str | None
    ) -> Page:
        limit_check(limit)
        relative_path(path, allow_empty=True)
        sha = await self.revision(repo, ref)
        url = f"{self.base(repo)}/content/{sha}/{qp(path)}"
        entries = await self.http.json("GET", url, params={"include_size": "true"})
        if isinstance(entries, dict):
            entries = [entries]
        if not isinstance(entries, list):
            raise DagsHubError("invalid_response", "Expected a repository file listing.")
        offset = 0
        if page_token:
            try:
                token_sha, token_path, offset_text = json.loads(page_token)
                offset = int(offset_text)
                if token_sha != sha or token_path != path or offset < 0:
                    raise ValueError()
            except (ValueError, TypeError):
                raise DagsHubError(
                    "invalid_argument", "Invalid page token or revision changed; restart listing."
                ) from None
        items = [
            {k: e[k] for k in ("path", "type", "size", "hash", "versioning") if k in e}
            for e in entries[offset : offset + limit]
        ]
        end = offset + limit
        return Page(
            repository=repo,
            source=url,
            revision=sha,
            items=items,
            next_page_token=json.dumps([sha, path, end]) if end < len(entries) else None,
            warnings=[
                "Listing includes versioned Git/DVC entries; file hashes identify their content."
            ],
        )

    async def read_file(self, repo: str, ref: str, path: str):
        relative_path(path)
        report_format(path)
        sha = await self.revision(repo, ref)
        url = f"{self.base(repo)}/raw/{sha}/{qp(path)}"
        data = await self.http.read("GET", url)
        return preview(data, repository=repo, source=url, path=path, revision=sha)


class MLflowAPI:
    def __init__(self, settings: Settings, transport: Transport):
        self.settings = settings
        self.http = transport

    def base(self, repo: str) -> str:
        return f"{HOST}/{self.settings.check_repo(repo)}.mlflow"

    async def request(self, repo: str, endpoint: str, *, method="GET", params=None, body=None):
        return await self.http.json(
            method, self.base(repo) + "/api/2.0/mlflow/" + endpoint, params=params, body=body
        )

    async def experiments(self, repo: str, limit: int, page_token: str | None) -> Page:
        limit_check(limit)
        body = {"max_results": limit}
        if page_token:
            body["page_token"] = page_token
        data = await self.request(repo, "experiments/search", method="POST", body=body)
        return Page(
            repository=repo,
            source=self.base(repo),
            items=data.get("experiments", []),
            next_page_token=data.get("next_page_token") or None,
        )

    async def search(
        self,
        repo: str,
        experiment_ids: list[str],
        filter_string: str,
        order_by: list[str],
        limit: int,
        page_token: str | None,
    ) -> Page:
        limit_check(limit)
        if not 1 <= len(experiment_ids) <= 100:
            raise DagsHubError("invalid_argument", "Provide 1–100 experiment IDs.")
        body = {
            "experiment_ids": experiment_ids,
            "filter": filter_string,
            "order_by": order_by,
            "max_results": limit,
            "run_view_type": "ACTIVE_ONLY",
        }
        if page_token:
            body["page_token"] = page_token
        data = await self.request(repo, "runs/search", method="POST", body=body)
        runs = [self.normalize(repo, r) for r in data.get("runs", [])]
        return Page(
            repository=repo,
            source=self.base(repo),
            items=[r.model_dump() for r in runs],
            next_page_token=data.get("next_page_token") or None,
        )

    def normalize(self, repo: str, data: dict) -> Run:
        info, content = data["info"], data.get("data", {})
        run_id = segment(info.get("run_id") or info["run_uuid"])
        metrics = {}
        warnings = []
        for entry in content.get("metrics", []):
            value = float(entry["value"])
            if not math.isfinite(value):
                value = None
                warnings.append(f"Metric {entry['key']} is non-finite and treated as missing.")
            metrics[entry["key"]] = Metric(
                value=value, step=entry.get("step", 0), timestamp=entry.get("timestamp", 0)
            )
        return Run(
            repository=repo,
            source=f"{self.base(repo)}/#/experiments/{q(info['experiment_id'])}/runs/{q(run_id)}",
            run_id=run_id,
            experiment_id=info["experiment_id"],
            status=info.get("status", "UNKNOWN"),
            artifact_uri=info.get("artifact_uri", ""),
            metrics=metrics,
            params={p["key"]: p["value"] for p in content.get("params", [])},
            tags={t["key"]: t["value"] for t in content.get("tags", [])},
            datasets=data.get("inputs", {}).get("dataset_inputs", []),
            warnings=warnings,
        )

    async def run(self, repo: str, run_id: str) -> Run:
        segment(run_id)
        data = await self.request(repo, "runs/get", params={"run_id": run_id})
        run = self.normalize(repo, data["run"])
        if run.run_id != run_id:
            raise DagsHubError("invalid_response", "Returned run ID differs from requested run.")
        return run

    async def history(
        self, repo: str, run_id: str, metric: str, limit: int, page_token: str | None
    ) -> Page:
        segment(run_id)
        limit_check(limit)
        if not metric:
            raise DagsHubError("invalid_argument", "Provide a metric name.")
        context = [repo, "history", run_id, metric]
        upstream, offset = read_cursor(page_token, context)
        # Use a fixed upstream size so changing local page sizes cannot shift offsets.
        params = {"run_id": run_id, "metric_key": metric, "max_results": 100}
        if upstream:
            params["page_token"] = upstream
        data = await self.request(repo, "metrics/get-history", params=params)
        items = data.get("metrics", [])
        for item in items:
            if not math.isfinite(float(item["value"])):
                item["value"] = None
        return Page(
            repository=repo,
            source=self.base(repo) + "/api/2.0/mlflow/metrics/get-history",
            items=items[offset : offset + limit],
            next_page_token=next_cursor(
                context, upstream, offset, len(items), limit, data.get("next_page_token")
            ),
            warnings=["History belongs to run " + run_id + ", metric " + metric],
        )

    async def artifacts(
        self, repo: str, run_id: str, path: str, limit: int, page_token: str | None
    ) -> Page:
        segment(run_id)
        relative_path(path, allow_empty=True)
        limit_check(limit)
        context = [repo, "artifacts", run_id, path]
        upstream, offset = read_cursor(page_token, context)
        params = {"run_id": run_id, "path": path}
        if upstream:
            params["page_token"] = upstream
        data = await self.request(repo, "artifacts/list", params=params)
        files = data.get("files", [])
        return Page(
            repository=repo,
            source=self.base(repo) + "/api/2.0/mlflow/artifacts/list",
            items=files[offset : offset + limit],
            next_page_token=next_cursor(
                context, upstream, offset, len(files), limit, data.get("next_page_token")
            ),
            warnings=["Artifact directories may change while a run is active."],
        )

    async def read_artifact(self, repo: str, run_id: str, path: str):
        relative_path(path)
        report_format(path)
        run = await self.run(repo, run_id)
        uri = urlsplit(run.artifact_uri)
        base = self.base(repo)
        if uri.query or uri.fragment or uri.username or uri.password:
            raise DagsHubError(
                "unsupported_storage", "Credentialed or signed artifact URLs are unsupported."
            )
        if uri.scheme == "mlflow-artifacts" and not uri.netloc:
            root = unquote(uri.path).strip("/")
            relative_path(root)
            url = base + "/api/2.0/mlflow-artifacts/artifacts/" + qp(root) + "/" + qp(path)
        elif uri.scheme == "https" and uri.netloc == "dagshub.com":
            # Only accept HTTP artifact roots scoped to this exact repository.
            prefixes = (f"/{repo}.mlflow/", f"/api/v1/repos/{repo}/storage/raw/", f"/{repo}/raw/")
            if not uri.path.startswith(prefixes):
                raise DagsHubError(
                    "unsupported_storage", "Artifact root is outside this repository."
                )
            decoded = unquote(uri.path).strip("/")
            relative_path(decoded)
            url = HOST + "/" + qp(decoded) + "/" + qp(path)
        else:
            raise DagsHubError(
                "unsupported_storage", "Only DagsHub HTTP or mlflow-artifacts storage is supported."
            )
        data = await self.http.read("GET", url)
        return preview(data, repository=repo, source=url, path=path, run_id=run_id)
