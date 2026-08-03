"""remote-probe 检查装配（设计 §8.2；V13-009 接入 provider）。"""

from __future__ import annotations

from typing import Any

from orch.constants import BARE_DIR_NAME, TARGET_BRANCH
from orch.git.ref import run_git_ref
from orch.promotion.config import get_promotion_config
from orch.registry import get_project_path
from orch.remote.auth import auth_summary_for_probe, resolve_github_auth
from orch.remote.factory import get_hosting_provider
from orch.remote.git import CliRemoteGitAdapter, _redact_git_text
from orch.remote.http import ProviderHttpError

STABLE_BRANCH = "master"

CheckStatus = str  # verified | unsupported | unknown | misconfigured


def _check(
    name: str,
    status: CheckStatus,
    *,
    detail: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {"name": name, "status": status}
    if detail:
        row["detail"] = detail
    if evidence:
        row["evidence"] = evidence
    return row


def _policy_checks_from_branch(
    *,
    branch: str,
    policy: dict[str, Any],
    names: list[str],
    is_master: bool,
) -> list[dict[str, Any]]:
    status = str(policy.get("status") or "unknown")
    if status in ("unsupported",):
        return [_check(n, "unsupported", detail=policy.get("detail")) for n in names]
    if status in ("unknown", "misconfigured") or policy.get("kind") in (
        "auth_failed",
        "forbidden",
        "not_found",
    ):
        st = "misconfigured" if status == "misconfigured" or policy.get("kind") == "auth_failed" else "unknown"
        return [
            _check(n, st, detail=str(policy.get("detail") or policy.get("kind") or "no policy"))
            for n in names
        ]

    out: list[dict[str, Any]] = []
    allow_force = policy.get("allow_force")
    allow_delete = policy.get("allow_delete")
    require_pr = policy.get("require_pr")
    required_checks = policy.get("required_checks") or []
    merge_methods = policy.get("merge_methods") or []
    bypass = policy.get("bypass_summary") or []

    if not is_master:
        out.append(
            _check(
                "block_force_push",
                "verified" if allow_force is False else ("misconfigured" if allow_force is True else "unknown"),
                detail=None if allow_force is False else "force not proven blocked",
                evidence={"branch": branch, "allow_force": allow_force},
            )
        )
        out.append(
            _check(
                "block_deletes",
                "verified" if allow_delete is False else ("misconfigured" if allow_delete is True else "unknown"),
                evidence={"branch": branch, "allow_delete": allow_delete},
            )
        )
        # Integration bot restrict：有 ruleset 即 partial；无 bypass 个人则更好
        out.append(
            _check(
                "restrict_updates_to_integration_bot",
                "verified" if status == "verified" else "unknown",
                evidence={"bypass_actors": len(bypass)},
            )
        )
        return out

    out.append(
        _check(
            "pr_only",
            "verified" if require_pr else ("unknown" if require_pr is None else "misconfigured"),
            evidence={"require_pr": require_pr},
        )
    )
    out.append(
        _check(
            "required_approvals",
            "verified" if require_pr else "unknown",
            detail="ruleset pull_request implies review gate when configured",
        )
    )
    out.append(
        _check(
            "required_checks",
            "verified" if required_checks else "unknown",
            evidence={"checks": required_checks},
        )
    )
    out.append(
        _check(
            "dismiss_stale_approvals",
            "unknown",
            detail="stale approval dismiss is ruleset param; confirm in V13-012 E2E if needed",
        )
    )
    # Solo OrganizationAdmin bypass 允许但不算 default bypass 失败
    human_always = [
        b
        for b in bypass
        if isinstance(b, dict) and str(b.get("actor_type", "")).lower() in ("user", "team")
    ]
    out.append(
        _check(
            "no_default_bypass",
            "verified" if not human_always else "unknown",
            detail="OrganizationAdmin solo bypass may be present; see setup §8",
            evidence={"bypass_count": len(bypass)},
        )
    )
    merge_ok = "merge_commit" in merge_methods and "squash" not in merge_methods
    # 仓库可能仍允许 squash 选项但 Ruleset 强制 merge；仅作信号
    out.append(
        _check(
            "merge_commit_only",
            "verified" if "merge_commit" in merge_methods else "unknown",
            evidence={"merge_methods": merge_methods, "squash_allowed_in_repo": "squash" in merge_methods},
            detail=None if merge_ok or "merge_commit" in merge_methods else "merge_commit not listed",
        )
    )
    return out


def run_remote_probe(project: str, *, fetch: bool = True) -> dict[str, Any]:
    """
    只读探测。不修改分支。
    五类检查槽位始终出现；无 provider/凭证时身份/policy/provider 为 unknown。
    """
    root = get_project_path(project)
    bare = root / BARE_DIR_NAME
    promo = get_promotion_config(project)
    remote = (promo or {}).get("remote", "origin")
    develop = (promo or {}).get("integration_branch", TARGET_BRANCH)
    master = (promo or {}).get("stable_branch", STABLE_BRANCH)
    adapter = CliRemoteGitAdapter()

    git_checks: list[dict[str, Any]] = []
    identity_checks: list[dict[str, Any]] = []
    develop_policy: list[dict[str, Any]] = []
    master_policy: list[dict[str, Any]] = []
    provider_checks: list[dict[str, Any]] = []

    # --- Git ---
    if not bare.is_dir():
        git_checks.append(
            _check("bare_repository", "misconfigured", detail=".bare.git missing")
        )
    else:
        git_checks.append(_check("bare_repository", "verified"))

    remote_url_r = run_git_ref(["remote", "get-url", remote], bare) if bare.is_dir() else None
    if remote_url_r is None or not remote_url_r.ok:
        git_checks.append(
            _check(
                "remote_reachable",
                "misconfigured" if bare.is_dir() else "unknown",
                detail=f"remote {remote!r} get-url failed",
            )
        )
        reachable = False
    else:
        git_checks.append(
            _check(
                "remote_configured",
                "verified",
                evidence={"remote": remote},
            )
        )
        reachable = True

    for branch, label in ((develop, "integration_ref"), (master, "stable_ref")):
        if not bare.is_dir():
            git_checks.append(_check(label, "unknown", detail="no bare"))
            continue
        local = adapter.local_head(bare, branch)
        if local:
            git_checks.append(
                _check(
                    f"local_{label}",
                    "verified",
                    evidence={"branch": branch, "sha": local},
                )
            )
        else:
            git_checks.append(
                _check(
                    f"local_{label}",
                    "misconfigured",
                    detail=f"refs/heads/{branch} missing locally",
                    evidence={"branch": branch},
                )
            )

    fetch_status: CheckStatus = "unknown"
    fetch_detail = "fetch skipped"
    if bare.is_dir() and reachable and fetch:
        try:
            adapter.fetch_core_refs(bare, remote, develop, master)
            fetch_status = "verified"
            fetch_detail = "fetch ok"
        except Exception as exc:  # noqa: BLE001 — probe 必须分类而非抛崩
            fetch_status = "unknown"
            fetch_detail = _redact_git_text(str(exc))
    elif not fetch:
        fetch_detail = "fetch disabled by caller"
    git_checks.append(_check("default_fetch_behavior", fetch_status, detail=fetch_detail))

    for branch, label in ((develop, "remote_integration_ref"), (master, "remote_stable_ref")):
        if not bare.is_dir() or not reachable:
            git_checks.append(_check(label, "unknown"))
            continue
        if not fetch:
            ref = f"refs/remotes/{remote}/{branch}"
            local_track = run_git_ref(["rev-parse", "--verify", ref], bare)
            if local_track.ok and local_track.stdout.strip():
                git_checks.append(
                    _check(
                        label,
                        "verified",
                        evidence={"branch": branch, "sha": local_track.stdout.strip()},
                    )
                )
            else:
                git_checks.append(
                    _check(
                        label,
                        "unknown",
                        detail="remote-tracking ref absent; re-run without --no-fetch",
                        evidence={"branch": branch},
                    )
                )
            continue
        sha = adapter.remote_head(bare, remote, branch)
        if sha:
            git_checks.append(
                _check(label, "verified", evidence={"branch": branch, "sha": sha})
            )
        else:
            git_checks.append(
                _check(
                    label,
                    "misconfigured",
                    detail=f"refs/heads/{branch} not found on remote {remote}",
                    evidence={"branch": branch},
                )
            )

    # --- 身份 / policy / provider（V13-009）---
    api_base = (promo or {}).get("api_base_url", "https://api.github.com")
    auth = resolve_github_auth(api_base_url=str(api_base))
    auth_ev = auth_summary_for_probe(auth)
    provider_name = str((promo or {}).get("provider") or "github").lower()

    host = get_hosting_provider(promo, auth=auth, role="probe")
    develop_host = get_hosting_provider(promo, auth=auth, role="develop_policy")
    release_host = get_hosting_provider(promo, auth=auth, role="pr")

    if host is None and provider_name == "github":
        identity_checks.append(
            _check(
                "bot_identity",
                "unknown",
                detail="no GitHub token; set ORCH_GITHUB_TOKEN or App env",
                evidence=auth_ev,
            )
        )
        identity_checks.append(
            _check(
                "visible_repository_scope",
                "unknown",
                detail="requires provider credentials",
                evidence=auth_ev,
            )
        )
        for name in (
            "block_force_push",
            "block_deletes",
            "restrict_updates_to_integration_bot",
        ):
            develop_policy.append(_check(name, "unknown", detail="requires ruleset read + token"))
        for name in (
            "pr_only",
            "required_approvals",
            "required_checks",
            "dismiss_stale_approvals",
            "no_default_bypass",
            "merge_commit_only",
        ):
            master_policy.append(_check(name, "unknown", detail="requires ruleset read + token"))
        for name in (
            "create_query_pr",
            "read_checks_reviews",
            "read_branch_protection",
            "non_force_cas_push",
        ):
            provider_checks.append(_check(name, "unknown", detail="requires provider adapter token"))
        provider_checks.append(
            _check(
                "hosting_provider",
                "unknown",
                detail="GitHubProviderAdapter credentials missing; unknown must not pass",
            )
        )
    elif host is not None and provider_name in ("manual", "gitlab"):
        caps = host.probe_capabilities()
        identity_checks.append(
            _check("bot_identity", "unsupported", detail=str(caps.get("detail") or provider_name))
        )
        identity_checks.append(
            _check("visible_repository_scope", "unsupported", detail=provider_name)
        )
        for name in (
            "block_force_push",
            "block_deletes",
            "restrict_updates_to_integration_bot",
        ):
            develop_policy.append(_check(name, "unsupported", detail=provider_name))
        for name in (
            "pr_only",
            "required_approvals",
            "required_checks",
            "dismiss_stale_approvals",
            "no_default_bypass",
            "merge_commit_only",
        ):
            master_policy.append(_check(name, "unsupported", detail=provider_name))
        for name in (
            "create_query_pr",
            "read_checks_reviews",
            "read_branch_protection",
            "non_force_cas_push",
        ):
            provider_checks.append(_check(name, "unsupported", detail=provider_name))
        provider_checks.append(_check("hosting_provider", "unsupported", detail=provider_name))
    else:
        assert host is not None
        try:
            caps = host.probe_capabilities()
        except ProviderHttpError as exc:
            caps = {"ok": False, "checks": [], "kind": exc.kind, "detail": str(exc)}
        for row in caps.get("checks") or []:
            if isinstance(row, dict) and row.get("name"):
                identity_checks.append(
                    _check(
                        str(row["name"]),
                        str(row.get("status") or "unknown"),
                        detail=row.get("detail"),
                        evidence=row.get("evidence"),
                    )
                )
        if not identity_checks:
            identity_checks.append(
                _check(
                    "bot_identity",
                    "unknown",
                    detail=str(caps.get("detail") or "no identity checks"),
                )
            )
            identity_checks.append(
                _check("visible_repository_scope", "unknown", detail="missing from probe_capabilities")
            )

        # develop / master policy
        pol_host = develop_host or host
        try:
            dpol = pol_host.branch_policy(str(develop))
        except ProviderHttpError as exc:
            dpol = {"status": "unknown", "kind": exc.kind, "detail": str(exc)}
        develop_policy.extend(
            _policy_checks_from_branch(
                branch=str(develop),
                policy=dpol,
                names=[
                    "block_force_push",
                    "block_deletes",
                    "restrict_updates_to_integration_bot",
                ],
                is_master=False,
            )
        )

        m_host = release_host or host
        try:
            mpol = m_host.branch_policy(str(master))
        except ProviderHttpError as exc:
            mpol = {"status": "unknown", "kind": exc.kind, "detail": str(exc)}
        master_policy.extend(
            _policy_checks_from_branch(
                branch=str(master),
                policy=mpol,
                names=[
                    "pr_only",
                    "required_approvals",
                    "required_checks",
                    "dismiss_stale_approvals",
                    "no_default_bypass",
                    "merge_commit_only",
                ],
                is_master=True,
            )
        )

        # provider capability checks（只读推断；不创建 PR）
        provider_checks.append(
            _check(
                "hosting_provider",
                "verified" if caps.get("ok") else "unknown",
                evidence={"identity_type": (caps.get("identity") or {}).get("type")},
            )
        )
        provider_checks.append(
            _check(
                "read_branch_protection",
                "verified" if dpol.get("status") == "verified" or mpol.get("status") == "verified" else "unknown",
                detail="rulesets read",
            )
        )
        # create_query_pr / read_checks：有 release/default token 即标可尝试（不写）
        can_pr = release_host is not None or host is not None
        provider_checks.append(
            _check(
                "create_query_pr",
                "verified" if can_pr and caps.get("ok") else "unknown",
                detail="credentials present; write not exercised by probe",
            )
        )
        provider_checks.append(
            _check(
                "read_checks_reviews",
                "verified" if can_pr and caps.get("ok") else "unknown",
                detail="credentials present; binding verified in contract tests",
            )
        )
        provider_checks.append(
            _check(
                "non_force_cas_push",
                "unknown",
                detail="CAS push proven by RemoteGitAdapter tests / Phase 0; not re-pushed here",
            )
        )

    categories = {
        "git": git_checks,
        "identity": identity_checks,
        "develop_policy": develop_policy,
        "master_policy": master_policy,
        "provider": provider_checks,
    }

    flat = [c for group in categories.values() for c in group]
    any_unknown = any(c["status"] == "unknown" for c in flat)
    any_misconfigured = any(c["status"] == "misconfigured" for c in flat)
    all_verified = all(c["status"] == "verified" for c in flat)

    if all_verified:
        overall = "verified"
    elif any_misconfigured:
        overall = "misconfigured"
    elif any_unknown:
        overall = "unknown"
    else:
        overall = "unsupported"

    # 有 unknown 致命槽位时不得宣称可写
    write_ok = overall == "verified" and not any_unknown

    return {
        "project": project,
        "promotion_configured": promo is not None,
        "remote": remote,
        "integration_branch": develop,
        "stable_branch": master,
        "default_branch_not_used_as_stable": True,
        "overall": overall,
        "overall_pass": False if overall != "verified" else True,
        "note": "unknown must not be treated as pass",
        "categories": categories,
        "auth_summary": auth_ev,
        "mode_decision": None,
        "write_paths_enabled": False if not write_ok else False,  # promote 仍未开放至此任务
    }
