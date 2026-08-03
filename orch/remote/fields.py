"""HostingProvider 领域字段契约（V13-009-2）。禁止 raw GitHub JSON 泄漏到 service。"""

from __future__ import annotations

from typing import Any, TypedDict


KIND_MERGE_NOT_SYNCABLE = "merge_not_syncable"
KIND_UNSUPPORTED = "unsupported"
KIND_MISCONFIGURED = "misconfigured"


class ProbeCheckRow(TypedDict, total=False):
    name: str
    status: str  # verified | unsupported | unknown | misconfigured
    detail: str
    evidence: dict[str, Any]


class ProbeCapabilitiesResult(TypedDict, total=False):
    ok: bool
    checks: list[ProbeCheckRow]
    identity: dict[str, Any]
    permissions_summary: dict[str, Any]
    kind: str
    detail: str


class BranchPolicyResult(TypedDict, total=False):
    exists: bool
    allow_force: bool | None
    allow_delete: bool | None
    require_pr: bool | None
    required_checks: list[str]
    bypass_summary: list[dict[str, Any]]
    merge_methods: list[str]
    enforcement: str | None
    kind: str
    detail: str
    status: str  # verified | unknown | misconfigured | unsupported


class PullRequestResult(TypedDict, total=False):
    external_id: str
    url: str
    head: str
    base: str
    head_sha: str
    base_sha: str
    state: str
    merged: bool
    merge_commit_sha: str | None
    mergeable: bool | None
    mergeable_state: str | None
    merge_method: str | None
    kind: str
    detail: str


class CheckRunRow(TypedDict, total=False):
    name: str
    conclusion: str | None
    status: str | None
    head_sha: str
    bound_to_source: bool


class ChecksResult(TypedDict, total=False):
    external_id: str
    source_sha: str
    checks: list[CheckRunRow]
    all_bound_required_passed: bool | None
    kind: str
    detail: str


class ReviewRow(TypedDict, total=False):
    actor: str
    state: str
    commit_id: str | None
    bound_to_source: bool
    is_bot: bool
    counts_as_code_owner: bool


class ReviewsResult(TypedDict, total=False):
    external_id: str
    source_sha: str
    reviews: list[ReviewRow]
    approved_bound_human_count: int
    kind: str
    detail: str


def unsupported_result(method: str) -> dict[str, Any]:
    return {
        "ok": False,
        "kind": KIND_UNSUPPORTED,
        "detail": f"{method} unsupported for this provider",
    }
