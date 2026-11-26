"""Local configuration and strict repository/path validation."""

import os
import re
from dataclasses import dataclass, field


class DagsHubError(Exception):
    """Safe, actionable error text that may be returned to a model."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


def segment(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", value) or value in {".", ".."}:
        raise DagsHubError("invalid_argument", "Invalid repository or run identifier.")
    return value


def relative_path(value: str, *, allow_empty: bool = False) -> str:
    if not value and allow_empty:
        return ""
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or "%" in value
        or any(ord(c) < 32 for c in value)
        or any(p in {"", ".", ".."} for p in value.split("/"))
    ):
        raise DagsHubError("invalid_argument", "Use a relative path without traversal or escapes.")
    return value


@dataclass(frozen=True)
class Settings:
    repositories: frozenset[str]
    username: str = field(default="", repr=False)
    token: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        if not self.repositories:
            raise DagsHubError("configuration", "Set DAGSHUB_REPOSITORIES to owner/repo entries.")
        for repo in self.repositories:
            parts = repo.split("/")
            if len(parts) != 2:
                raise DagsHubError("configuration", "Repositories must use owner/repo format.")
            for part in parts:
                segment(part)
        if bool(self.username) != bool(self.token):
            raise DagsHubError("configuration", "Set both DAGSHUB_USERNAME and DAGSHUB_TOKEN.")

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            frozenset(
                r.strip() for r in os.getenv("DAGSHUB_REPOSITORIES", "").split(",") if r.strip()
            ),
            os.getenv("DAGSHUB_USERNAME", ""),
            os.getenv("DAGSHUB_TOKEN", ""),
        )

    def check_repo(self, repo: str) -> str:
        if repo not in self.repositories:
            raise DagsHubError(
                "repository_not_allowed", "Repository is not in DAGSHUB_REPOSITORIES."
            )
        return repo
