"""HostingProvider 工厂（V13-009-4）。"""

from __future__ import annotations

from typing import Any

from orch.remote.adapter import HostingProviderAdapter
from orch.remote.auth import ResolvedGitHubAuth, resolve_github_auth
from orch.remote.github import GitHubProviderAdapter
from orch.remote.gitlab import GitLabProviderAdapter
from orch.remote.http import GitHubHttpClient
from orch.remote.manual import ManualProviderAdapter


def get_hosting_provider(
    entry: dict[str, Any] | None,
    *,
    auth: ResolvedGitHubAuth | None = None,
    role: str = "probe",
) -> HostingProviderAdapter | None:
    """
    按 config.provider 返回 adapter。
    无凭证时返回 None（probe 填 unknown）；manual/gitlab 始终返回占位实例。
    """
    if not entry:
        return None
    provider = str(entry.get("provider") or "github").strip().lower()
    if provider == "manual":
        return ManualProviderAdapter()
    if provider == "gitlab":
        return GitLabProviderAdapter()
    if provider != "github":
        return None

    repository = str(entry.get("repository") or "").strip()
    api_base = str(entry.get("api_base_url") or "https://api.github.com").rstrip("/")
    if not repository:
        return None

    resolved = auth if auth is not None else resolve_github_auth(api_base_url=api_base)
    if role == "develop_policy":
        creds = resolved.for_develop_policy()
    elif role in ("pr", "release", "checks"):
        creds = resolved.for_pr_and_checks()
    else:
        creds = resolved.best_for_probe()
    if creds is None:
        return None

    client = GitHubHttpClient(creds.token, api_base_url=api_base)
    return GitHubProviderAdapter(client, repository=repository)
