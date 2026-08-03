"""GitHub HostingProviderAdapter（V13-009-3）。只返回领域字段。"""

from __future__ import annotations

import time
from typing import Any

from orch.remote.fields import (
    KIND_MERGE_NOT_SYNCABLE,
    BranchPolicyResult,
    ChecksResult,
    ProbeCapabilitiesResult,
    PullRequestResult,
    ReviewsResult,
)
from orch.remote.http import GitHubHttpClient, ProviderHttpError

# mergeable=null 有界退避
MERGEABLE_MAX_ATTEMPTS = 5
MERGEABLE_TOTAL_TIMEOUT_SEC = 15.0
MERGEABLE_SLEEP_SEC = 0.4


class GitHubProviderAdapter:
    """REST → 稳定领域字段；禁止把 raw GitHub JSON 返回给调用方。"""

    def __init__(
        self,
        client: GitHubHttpClient,
        *,
        repository: str,
        sleep: Any = time.sleep,
    ) -> None:
        if "/" not in repository:
            raise ValueError("repository must be owner/name")
        self._client = client
        self.repository = repository
        self._owner, self._repo = repository.split("/", 1)
        self._sleep = sleep

    def _repo_path(self, suffix: str = "") -> str:
        base = f"/repos/{self._owner}/{self._repo}"
        return f"{base}{suffix}" if suffix else base

    def probe_capabilities(self) -> ProbeCapabilitiesResult:
        checks: list[dict[str, Any]] = []
        identity: dict[str, Any] = {}
        permissions: dict[str, Any] = {}
        try:
            # installation token → /installation；PAT → /user
            try:
                inst = self._client.get_json("/installation")
                if isinstance(inst, dict) and inst.get("id") is not None:
                    identity = {
                        "type": "installation",
                        "id": inst.get("id"),
                        "app_id": inst.get("app_id"),
                        "account": (inst.get("account") or {}).get("login"),
                    }
                    checks.append({"name": "bot_identity", "status": "verified"})
            except ProviderHttpError as exc:
                if exc.kind not in ("auth_failed", "forbidden", "not_found"):
                    raise
                user = self._client.get_json("/user")
                if isinstance(user, dict):
                    identity = {
                        "type": "user",
                        "login": user.get("login"),
                        "id": user.get("id"),
                    }
                    checks.append({"name": "bot_identity", "status": "verified"})
                else:
                    checks.append(
                        {
                            "name": "bot_identity",
                            "status": "misconfigured",
                            "detail": "unexpected /user payload",
                        }
                    )

            repo = self._client.get_json(self._repo_path())
            if isinstance(repo, dict):
                permissions = {
                    "full_name": repo.get("full_name"),
                    "private": repo.get("private"),
                    "permissions": {
                        k: bool(v)
                        for k, v in (repo.get("permissions") or {}).items()
                        if k in ("admin", "maintain", "push", "triage", "pull")
                    },
                }
                checks.append(
                    {
                        "name": "visible_repository_scope",
                        "status": "verified",
                        "evidence": {"repository": repo.get("full_name")},
                    }
                )
            else:
                checks.append(
                    {
                        "name": "visible_repository_scope",
                        "status": "misconfigured",
                        "detail": "unexpected repo payload",
                    }
                )
        except ProviderHttpError as exc:
            status = "misconfigured" if exc.kind in ("auth_failed", "forbidden") else "unknown"
            checks.append(
                {
                    "name": "bot_identity",
                    "status": status,
                    "detail": f"{exc.kind}",
                }
            )
            checks.append(
                {
                    "name": "visible_repository_scope",
                    "status": status,
                    "detail": f"{exc.kind}",
                }
            )
            return {
                "ok": False,
                "checks": checks,
                "identity": identity,
                "permissions_summary": permissions,
                "kind": exc.kind,
                "detail": str(exc),
            }

        ok = all(c.get("status") == "verified" for c in checks)
        return {
            "ok": ok,
            "checks": checks,
            "identity": identity,
            "permissions_summary": permissions,
        }

    def branch_policy(self, branch: str) -> BranchPolicyResult:
        """优先 REST rulesets；权限不足 → unknown/misconfigured，不得 verified。"""
        try:
            rulesets = self._client.get_json(self._repo_path("/rulesets"))
        except ProviderHttpError as exc:
            if exc.kind in ("auth_failed", "forbidden"):
                return {
                    "exists": False,
                    "allow_force": None,
                    "allow_delete": None,
                    "require_pr": None,
                    "required_checks": [],
                    "bypass_summary": [],
                    "merge_methods": [],
                    "status": "misconfigured" if exc.kind == "auth_failed" else "unknown",
                    "kind": exc.kind,
                    "detail": "rulesets read denied; cannot mark verified",
                }
            if exc.kind == "not_found":
                return {
                    "exists": False,
                    "allow_force": None,
                    "allow_delete": None,
                    "require_pr": None,
                    "required_checks": [],
                    "bypass_summary": [],
                    "merge_methods": [],
                    "status": "unknown",
                    "kind": exc.kind,
                    "detail": "rulesets endpoint unavailable",
                }
            return {
                "exists": False,
                "allow_force": None,
                "allow_delete": None,
                "require_pr": None,
                "required_checks": [],
                "bypass_summary": [],
                "merge_methods": [],
                "status": "unknown",
                "kind": exc.kind,
                "detail": str(exc),
            }

        if not isinstance(rulesets, list):
            return {
                "exists": False,
                "allow_force": None,
                "allow_delete": None,
                "require_pr": None,
                "required_checks": [],
                "bypass_summary": [],
                "merge_methods": [],
                "status": "misconfigured",
                "detail": "unexpected rulesets payload",
            }

        matching: list[dict[str, Any]] = []
        for rs in rulesets:
            if not isinstance(rs, dict):
                continue
            cond = rs.get("conditions") or {}
            ref_name = (cond.get("ref_name") or {}) if isinstance(cond, dict) else {}
            include = ref_name.get("include") or []
            target = f"refs/heads/{branch}"
            if any(
                inc in (target, f"refs/heads/{branch}", branch, "~DEFAULT_BRANCH")
                or (isinstance(inc, str) and inc.endswith(f"/{branch}"))
                for inc in include
            ) or not include:
                # 无 include 时仍记录；精确匹配优先
                if include and not any(
                    _ruleset_matches_branch(inc, branch) for inc in include
                ):
                    continue
                matching.append(rs)

        allow_force = True
        allow_delete = True
        require_pr = False
        required_checks: list[str] = []
        bypass_summary: list[dict[str, Any]] = []
        merge_methods: list[str] = []

        for rs in matching:
            rules = rs.get("rules") or []
            # 部分 list API 不含 rules；尝试 detail
            if not rules and rs.get("id") is not None:
                try:
                    detail = self._client.get_json(self._repo_path(f"/rulesets/{rs['id']}"))
                    if isinstance(detail, dict):
                        rules = detail.get("rules") or []
                        rs = detail
                except ProviderHttpError:
                    pass
            for actor in rs.get("bypass_actors") or []:
                if isinstance(actor, dict):
                    bypass_summary.append(
                        {
                            "actor_id": actor.get("actor_id"),
                            "actor_type": actor.get("actor_type"),
                            "bypass_mode": actor.get("bypass_mode"),
                        }
                    )
            for rule in rules:
                if not isinstance(rule, dict):
                    continue
                rtype = rule.get("type")
                params = rule.get("parameters") or {}
                if rtype == "non_fast_forward":
                    allow_force = False
                if rtype == "deletion":
                    allow_delete = False
                if rtype == "pull_request":
                    require_pr = True
                if rtype == "required_status_checks":
                    for ctx in params.get("required_status_checks") or []:
                        if isinstance(ctx, dict) and ctx.get("context"):
                            required_checks.append(str(ctx["context"]))
                        elif isinstance(ctx, str):
                            required_checks.append(ctx)

        # merge methods from repo settings (best-effort)
        try:
            repo = self._client.get_json(self._repo_path())
            if isinstance(repo, dict):
                if repo.get("allow_merge_commit"):
                    merge_methods.append("merge_commit")
                if repo.get("allow_squash_merge"):
                    merge_methods.append("squash")
                if repo.get("allow_rebase_merge"):
                    merge_methods.append("rebase")
        except ProviderHttpError:
            pass

        return {
            "exists": True,
            "allow_force": allow_force if matching else None,
            "allow_delete": allow_delete if matching else None,
            "require_pr": require_pr if matching else None,
            "required_checks": sorted(set(required_checks)),
            "bypass_summary": bypass_summary,
            "merge_methods": merge_methods,
            "enforcement": "active" if matching else None,
            "status": "verified" if matching else "unknown",
            "detail": None if matching else f"no ruleset matched branch {branch}",
        }

    def create_promotion_pr(
        self,
        head: str,
        base: str,
        title: str,
        body: str,
    ) -> PullRequestResult:
        # 幂等：同 head/base 已开 PR 则复用
        existing = self._find_open_pr(head=head, base=base)
        if existing is not None:
            return existing

        try:
            raw = self._client.post_json(
                self._repo_path("/pulls"),
                json_body={
                    "title": title,
                    "head": head,
                    "base": base,
                    "body": body,
                },
            )
        except ProviderHttpError as exc:
            return {
                "external_id": "",
                "url": "",
                "head": head,
                "base": base,
                "head_sha": "",
                "base_sha": "",
                "state": "error",
                "merged": False,
                "merge_commit_sha": None,
                "mergeable": None,
                "mergeable_state": None,
                "kind": exc.kind,
                "detail": str(exc),
            }
        return self._pr_from_raw(raw)

    def get_pr(self, external_id: str) -> PullRequestResult:
        try:
            raw = self._client.get_json(self._repo_path(f"/pulls/{external_id}"))
        except ProviderHttpError as exc:
            return {
                "external_id": str(external_id),
                "url": "",
                "head": "",
                "base": "",
                "head_sha": "",
                "base_sha": "",
                "state": "error",
                "merged": False,
                "merge_commit_sha": None,
                "mergeable": None,
                "mergeable_state": None,
                "kind": exc.kind,
                "detail": str(exc),
            }
        result = self._pr_from_raw(raw)
        # mergeable=null → 有界退避
        if result.get("mergeable") is None and result.get("state") == "open":
            result = self._wait_mergeable(external_id, result)
        if result.get("merged") and result.get("merge_method") in ("squash", "rebase"):
            result["kind"] = KIND_MERGE_NOT_SYNCABLE
            result["detail"] = "squash/rebase merge is not syncable to develop"
        return result

    def get_checks(self, external_id: str, source_sha: str) -> ChecksResult:
        try:
            # check-runs for commit
            raw = self._client.get_json(
                self._repo_path(f"/commits/{source_sha}/check-runs"),
                query={"per_page": "100"},
            )
        except ProviderHttpError as exc:
            return {
                "external_id": str(external_id),
                "source_sha": source_sha,
                "checks": [],
                "all_bound_required_passed": None,
                "kind": exc.kind,
                "detail": str(exc),
            }
        runs = []
        if isinstance(raw, dict):
            runs = raw.get("check_runs") or []
        checks: list[dict[str, Any]] = []
        for run in runs:
            if not isinstance(run, dict):
                continue
            head_sha = str(run.get("head_sha") or "")
            bound = head_sha.lower() == source_sha.lower()
            if not bound:
                continue  # 仅统计绑定该 SHA
            checks.append(
                {
                    "name": str(run.get("name") or ""),
                    "conclusion": run.get("conclusion"),
                    "status": run.get("status"),
                    "head_sha": head_sha,
                    "bound_to_source": True,
                }
            )
        return {
            "external_id": str(external_id),
            "source_sha": source_sha,
            "checks": checks,
            "all_bound_required_passed": None,
        }

    def get_reviews(self, external_id: str, source_sha: str) -> ReviewsResult:
        try:
            raw = self._client.get_json(self._repo_path(f"/pulls/{external_id}/reviews"))
        except ProviderHttpError as exc:
            return {
                "external_id": str(external_id),
                "source_sha": source_sha,
                "reviews": [],
                "approved_bound_human_count": 0,
                "kind": exc.kind,
                "detail": str(exc),
            }
        reviews: list[dict[str, Any]] = []
        approved_human = 0
        if isinstance(raw, list):
            for rev in raw:
                if not isinstance(rev, dict):
                    continue
                user = rev.get("user") or {}
                login = str(user.get("login") or "")
                utype = str(user.get("type") or "")
                is_bot = utype.lower() == "bot" or login.endswith("[bot]")
                commit_id = rev.get("commit_id")
                bound = bool(commit_id) and str(commit_id).lower() == source_sha.lower()
                state = str(rev.get("state") or "")
                counts = bound and state.upper() == "APPROVED" and not is_bot
                if counts:
                    approved_human += 1
                reviews.append(
                    {
                        "actor": login,
                        "state": state,
                        "commit_id": str(commit_id) if commit_id else None,
                        "bound_to_source": bound,
                        "is_bot": is_bot,
                        "counts_as_code_owner": False if is_bot else counts,
                    }
                )
        return {
            "external_id": str(external_id),
            "source_sha": source_sha,
            "reviews": reviews,
            "approved_bound_human_count": approved_human,
        }

    def _find_open_pr(self, *, head: str, base: str) -> PullRequestResult | None:
        try:
            raw = self._client.get_json(
                self._repo_path("/pulls"),
                query={"state": "open", "base": base, "per_page": "30"},
            )
        except ProviderHttpError:
            return None
        if not isinstance(raw, list):
            return None
        head_ref = head.split(":")[-1]
        for pr in raw:
            if not isinstance(pr, dict):
                continue
            pr_head = str((pr.get("head") or {}).get("ref") or "")
            pr_base = str((pr.get("base") or {}).get("ref") or "")
            if pr_base != base:
                continue
            if pr_head == head or pr_head == head_ref:
                return self._pr_from_raw(pr)
        return None

    def _wait_mergeable(
        self, external_id: str, current: PullRequestResult
    ) -> PullRequestResult:
        deadline = time.monotonic() + MERGEABLE_TOTAL_TIMEOUT_SEC
        attempt = 0
        result = current
        while (
            result.get("mergeable") is None
            and attempt < MERGEABLE_MAX_ATTEMPTS
            and time.monotonic() < deadline
        ):
            attempt += 1
            self._sleep(MERGEABLE_SLEEP_SEC)
            try:
                raw = self._client.get_json(self._repo_path(f"/pulls/{external_id}"))
            except ProviderHttpError:
                break
            result = self._pr_from_raw(raw)
        return result

    def _pr_from_raw(self, raw: Any) -> PullRequestResult:
        if not isinstance(raw, dict):
            return {
                "external_id": "",
                "url": "",
                "head": "",
                "base": "",
                "head_sha": "",
                "base_sha": "",
                "state": "error",
                "merged": False,
                "merge_commit_sha": None,
                "mergeable": None,
                "mergeable_state": None,
                "kind": "validation",
                "detail": "unexpected PR payload",
            }
        head = raw.get("head") or {}
        base = raw.get("base") or {}
        merged = bool(raw.get("merged"))
        merge_commit_sha = raw.get("merge_commit_sha")
        # GitHub 不直接给 merge_method；用 merged + commits 启发
        merge_method = None
        parents_count = None
        if merged and merge_commit_sha:
            try:
                commit = self._client.get_json(
                    self._repo_path(f"/git/commits/{merge_commit_sha}")
                )
                if isinstance(commit, dict):
                    parents = commit.get("parents") or []
                    parents_count = len(parents)
                    if parents_count >= 2:
                        merge_method = "merge_commit"
                    elif parents_count == 1:
                        merge_method = "squash"  # or rebase; both not syncable
            except ProviderHttpError:
                pass

        out: PullRequestResult = {
            "external_id": str(raw.get("number") or ""),
            "url": str(raw.get("html_url") or ""),
            "head": str(head.get("ref") or ""),
            "base": str(base.get("ref") or ""),
            "head_sha": str(head.get("sha") or ""),
            "base_sha": str(base.get("sha") or ""),
            "state": str(raw.get("state") or ""),
            "merged": merged,
            "merge_commit_sha": str(merge_commit_sha) if merge_commit_sha else None,
            "mergeable": raw.get("mergeable"),
            "mergeable_state": raw.get("mergeable_state"),
            "merge_method": merge_method,
        }
        if merged and merge_method in ("squash", "rebase"):
            out["kind"] = KIND_MERGE_NOT_SYNCABLE
            out["detail"] = "squash/rebase merge is not syncable to develop"
        return out


def _ruleset_matches_branch(include: Any, branch: str) -> bool:
    if not isinstance(include, str):
        return False
    if include in (f"refs/heads/{branch}", branch, "refs/heads/*", "~ALL"):
        return True
    if include.startswith("refs/heads/") and include.endswith("*"):
        prefix = include[len("refs/heads/") : -1]
        return branch.startswith(prefix)
    return include.endswith(f"/{branch}")
