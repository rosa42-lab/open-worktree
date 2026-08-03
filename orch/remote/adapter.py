"""远端适配器 Protocol（设计 §12.1–§12.2）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class RemoteGitAdapter(Protocol):
    def fetch_core_refs(
        self,
        bare: Path,
        remote: str,
        develop: str,
        master: str,
    ) -> None: ...

    def remote_head(self, bare: Path, remote: str, branch: str) -> str | None: ...

    def local_head(self, bare: Path, branch: str) -> str | None: ...

    def is_ancestor(self, bare: Path, older: str, newer: str) -> bool: ...

    def push_fast_forward(
        self,
        bare: Path,
        remote: str,
        source_ref: str,
        target_ref: str,
        expected_old_sha: str,
        new_sha: str,
    ) -> None: ...

    def sync_verified_merge(
        self,
        bare: Path,
        source_sha: str,
        published_sha: str,
    ) -> None: ...


@runtime_checkable
class HostingProviderAdapter(Protocol):
    def probe_capabilities(self) -> dict[str, Any]: ...

    def branch_policy(self, branch: str) -> dict[str, Any]: ...

    def create_promotion_pr(
        self,
        head: str,
        base: str,
        title: str,
        body: str,
    ) -> dict[str, Any]: ...

    def get_pr(self, external_id: str) -> dict[str, Any]: ...

    def get_checks(self, external_id: str, source_sha: str) -> dict[str, Any]: ...

    def get_reviews(self, external_id: str, source_sha: str) -> dict[str, Any]: ...
