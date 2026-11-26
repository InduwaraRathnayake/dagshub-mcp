import json

import httpx
import pytest
from conftest import mock_transport, raw_run

from dagshub_mcp.api import DagsHubAPI, MLflowAPI
from dagshub_mcp.config import DagsHubError

SHA = "a" * 40


async def test_experiment_and_search_pagination(settings):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["authorization"].startswith("Basic ")
        body = json.loads(request.content)
        assert body["page_token"] == "next"
        if request.url.path.endswith("experiments/search"):
            return httpx.Response(
                200, json={"experiments": [{"experiment_id": "1"}], "next_page_token": "last"}
            )
        assert body["filter"] == "metrics.recall > 0.8"
        return httpx.Response(200, json={"runs": [raw_run()], "next_page_token": "last"})

    transport = mock_transport(settings, handler)
    api = MLflowAPI(settings, transport)
    page = await api.experiments("owner/repo", 10, "next")
    assert page.next_page_token == "last"
    page = await api.search("owner/repo", ["1"], "metrics.recall > 0.8", [], 10, "next")
    assert page.items[0]["metrics"]["recall"]["step"] == 5
    assert len(calls) == 2
    await transport.close()


async def test_revision_pinned_files_and_paging(settings):
    def handler(request):
        if request.url.path.endswith("/commits"):
            assert request.url.params["sha"] in {"main", SHA}
            return httpx.Response(200, json=[{"id": SHA}])
        assert SHA in request.url.path
        if "/content/" in request.url.path:
            return httpx.Response(200, json=[{"path": "a.json", "type": "file"}, {"path": "b.csv"}])
        return httpx.Response(200, content=b'{"recall":0.9}')

    transport = mock_transport(settings, handler)
    api = DagsHubAPI(settings, transport)
    page = await api.list_files("owner/repo", "main", "", 1, None)
    assert page.revision == SHA
    second = await api.list_files("owner/repo", SHA, "", 1, page.next_page_token)
    assert second.items[0]["path"] == "b.csv"
    result = await api.read_file("owner/repo", SHA, "a.json")
    assert result.revision == SHA
    await transport.close()


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "authentication"),
        (403, "authentication"),
        (404, "not_found"),
        (500, "upstream_error"),
        (302, "unsupported_redirect"),
    ],
)
async def test_http_errors_are_sanitized(settings, status, code):
    transport = mock_transport(settings, lambda _: httpx.Response(status, text="test-secret"))
    with pytest.raises(DagsHubError, match=code) as error:
        await transport.json("GET", "https://dagshub.com/api/test")
    assert "test-secret" not in str(error.value)
    await transport.close()


async def test_rate_limit_retry_and_exhaustion(settings):
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(429, headers={"Retry-After": "0"})

    transport = mock_transport(settings, handler)
    with pytest.raises(DagsHubError, match="rate_limited"):
        await transport.json("GET", "https://dagshub.com/api/test")
    assert count == 3
    await transport.close()


async def test_retry_success_and_timeout(settings, monkeypatch):
    async def no_sleep(_):
        pass

    monkeypatch.setattr("dagshub_mcp.api.asyncio.sleep", no_sleep)
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        if count == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": True})

    transport = mock_transport(settings, handler)
    assert await transport.json("GET", "https://dagshub.com/test") == {"ok": True}
    await transport.close()

    def timeout(request):
        raise httpx.ReadTimeout("test-secret", request=request)

    transport = mock_transport(settings, timeout)
    with pytest.raises(DagsHubError, match="timeout"):
        await transport.json("GET", "https://dagshub.com/test")
    await transport.close()


async def test_transport_bounds_invalid_json_and_readonly(settings):
    transport = mock_transport(settings, lambda _: httpx.Response(200, content=b"12345"))
    with pytest.raises(DagsHubError, match="response_too_large"):
        await transport.read("GET", "https://dagshub.com/test", max_bytes=4)
    with pytest.raises(DagsHubError, match="read_only"):
        await transport.read("DELETE", "https://dagshub.com/test")
    with pytest.raises(DagsHubError, match="unsupported_storage"):
        await transport.read("GET", "https://evil.example/test")
    await transport.close()
    transport = mock_transport(settings, lambda _: httpx.Response(200, text="<html>"))
    with pytest.raises(DagsHubError, match="invalid_response"):
        await transport.json("GET", "https://dagshub.com/test")
    await transport.close()


@pytest.mark.parametrize(
    "uri",
    [
        "mlflow-artifacts:/1/base/artifacts",
        "https://dagshub.com/owner/repo.mlflow/artifacts/1/base",
    ],
)
async def test_artifact_read(settings, uri):
    def handler(request):
        if request.url.path.endswith("runs/get"):
            return httpx.Response(200, json={"run": raw_run(artifact_uri=uri)})
        assert request.url.path.endswith("/report.json")
        return httpx.Response(200, content=b'{"recall":0.9}')

    transport = mock_transport(settings, handler)
    result = await MLflowAPI(settings, transport).read_artifact("owner/repo", "base", "report.json")
    assert result.run_id == "base"
    await transport.close()


@pytest.mark.parametrize(
    "uri",
    [
        "s3://bucket/model",
        "file:///tmp/model",
        "https://evil.example/data",
        "https://dagshub.com/other/repo.mlflow/data",
        "https://dagshub.com/owner/repo.mlflow/../other/data",
        "https://dagshub.com/owner/repo.mlflow/%2e%2e/data",
    ],
)
async def test_unsupported_artifact_storage(settings, uri):
    transport = mock_transport(
        settings, lambda _: httpx.Response(200, json={"run": raw_run(artifact_uri=uri)})
    )
    with pytest.raises(DagsHubError):
        await MLflowAPI(settings, transport).read_artifact("owner/repo", "base", "report.json")
    await transport.close()


async def test_allowlist_and_history(settings):
    def handler(request):
        assert request.url.params["metric_key"] == "recall"
        assert request.url.params["page_token"] == "next"
        return httpx.Response(
            200,
            json={
                "metrics": [{"key": "recall", "value": 0.8, "step": 1}],
                "next_page_token": "last",
            },
        )

    transport = mock_transport(settings, handler)
    api = MLflowAPI(settings, transport)
    with pytest.raises(DagsHubError, match="repository_not_allowed"):
        await api.run("other/repo", "base")
    token = json.dumps(
        {"context": ["owner/repo", "history", "base", "recall"], "upstream": "next", "offset": 0}
    )
    page = await api.history("owner/repo", "base", "recall", 10, token)
    assert json.loads(page.next_page_token)["upstream"] == "last"
    await transport.close()


@pytest.mark.parametrize("kind", ["history", "artifacts"])
async def test_local_and_upstream_pagination_never_skips(settings, kind):
    def handler(request):
        tail = request.url.params.get("page_token") == "tail"
        values = [3, 4] if tail else [0, 1, 2]
        key = "metrics" if kind == "history" else "files"
        entries = (
            [{"value": n, "step": n} for n in values]
            if kind == "history"
            else [{"path": str(n)} for n in values]
        )
        return httpx.Response(200, json={key: entries, "next_page_token": None if tail else "tail"})

    transport = mock_transport(settings, handler)
    api = MLflowAPI(settings, transport)
    token, found = None, []
    while True:
        page = (
            await api.history("owner/repo", "base", "recall", 2, token)
            if kind == "history"
            else await api.artifacts("owner/repo", "base", "", 2, token)
        )
        found.extend(e["step"] if kind == "history" else int(e["path"]) for e in page.items)
        token = page.next_page_token
        if not token:
            break
    assert found == [0, 1, 2, 3, 4]
    await transport.close()
