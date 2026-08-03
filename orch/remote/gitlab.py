"""GitLab hosting provider 占位（V13-009-4）。"""

from __future__ import annotations

from typing import Any

from orch.remote.fields import unsupported_result


class GitLabProviderAdapter:
    def probe_capabilities(self) -> dict[str, Any]:
        return unsupported_result("probe_capabilities")

    def branch_policy(self, branch: str) -> dict[str, Any]:
        out = unsupported_result("branch_policy")
        out["exists"] = False
        out["status"] = "unsupported"
        out["required_checks"] = []
        out["bypass_summary"] = []
        out["merge_methods"] = []
        out["branch"] = branch
        return out

    def create_promotion_pr(
        self, head: str, base: str, title: str, body: str
    ) -> dict[str, Any]:
        return unsupported_result("create_promotion_pr")

    def get_pr(self, external_id: str) -> dict[str, Any]:
        return unsupported_result("get_pr")

    def get_checks(self, external_id: str, source_sha: str) -> dict[str, Any]:
        return unsupported_result("get_checks")

    def get_reviews(self, external_id: str, source_sha: str) -> dict[str, Any]:
        return unsupported_result("get_reviews")
