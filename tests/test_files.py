import pytest

from dagshub_mcp.config import DagsHubError, Settings, relative_path
from dagshub_mcp.files import MAX_BYTES, preview


def read(data, path="report.json"):
    return preview(data, repository="owner/repo", source="https://dagshub.com/report", path=path)


def test_json_preview_and_text_limit():
    result = read(b'{"accuracy": 0.9}')
    assert result.format == "json"
    assert not result.truncated
    result = read(('"' + "x" * 25_000 + '"').encode())
    assert result.truncated and len(result.text) == 20_000


def test_csv_bounds():
    result = read(("a,b\n" * 150).encode(), "results.csv")
    assert len(result.rows) == 100
    assert result.truncated
    assert result.text == ""
    result = read(("a" * 25_000).encode(), "results.csv")
    assert result.rows == [] and result.truncated


@pytest.mark.parametrize(
    "data,path",
    [
        (b"{", "a.json"),
        (b'{"x":NaN}', "a.json"),
        (b"\xff", "a.txt"),
        (b"a\x00", "a.txt"),
        (b"pickle", "a.pkl"),
        (b"x" * (MAX_BYTES + 1), "a.txt"),
    ],
)
def test_reject_invalid_or_large(data, path):
    with pytest.raises(DagsHubError):
        read(data, path)


@pytest.mark.parametrize("path", ["../x", "/x", "a/../b", "a\\b", "%2e%2e/x", "a//b", "a\x00"])
def test_path_validation(path):
    with pytest.raises(DagsHubError):
        relative_path(path)


def test_configuration_and_secret_repr():
    config = Settings(frozenset({"owner/repo"}), "user", "secret")
    assert "secret" not in repr(config)
    with pytest.raises(DagsHubError):
        config.check_repo("other/repo")
    with pytest.raises(DagsHubError):
        Settings(frozenset())
    with pytest.raises(DagsHubError):
        Settings(frozenset({"owner/repo"}), "user")
