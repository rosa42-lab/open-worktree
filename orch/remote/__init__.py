"""远端包（v1.3）。"""

from __future__ import annotations

from orch.remote.adapter import HostingProviderAdapter, RemoteGitAdapter
from orch.remote.factory import get_hosting_provider
from orch.remote.git import CliRemoteGitAdapter
from orch.remote.github import GitHubProviderAdapter

__all__ = [
    "RemoteGitAdapter",
    "HostingProviderAdapter",
    "CliRemoteGitAdapter",
    "GitHubProviderAdapter",
    "get_hosting_provider",
]
