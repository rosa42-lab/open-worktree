"""release-create / release-status（V13-010）。"""

from __future__ import annotations

from typing import Any

from orch.constants import BARE_DIR_NAME, project_lock_path
from orch.db import immediate_transaction, open_project_db
from orch.errors import ValidationError
from orch.locks import acquire, release
from orch.promotion import repo as promo_repo
from orch.promotion.auth_provider import get_release_provider
from orch.promotion.precheck import PrecheckBlocked, PrecheckManual, PrecheckRetryable
from orch.promotion.release_precheck import run_release_precheck
from orch.promotion.state import assert_master_transition
from orch.registry import get_project_path
from orch.remote.git import CliRemoteGitAdapter
from orch.util import utc_now_iso
from orch.validate import validate_project_name


def _transition(
    conn: Any,
    run: dict[str, Any],
    to_state: str,
    *,
    event_type: str,
    source: str,
    detail: Any = None,
    **fields: Any,
) -> dict[str, Any]:
    assert_master_transition(run["state"], to_state)
    payload = {"state": to_state, **fields}
    if to_state in ("released", "cancelled"):
        payload.setdefault("finished_at", utc_now_iso())
    updated = promo_repo.update_run_fields(conn, run["id"], **payload)
    promo_repo.append_event(
        conn,
        promotion_id=run["id"],
        event_type=event_type,
        source=source,
        detail=detail if detail is not None else {"to": to_state},
    )
    return updated


def release_create(
    project: str,
    *,
    verification: str,
    title: str | None = None,
    execute: bool = False,
    fetch: bool = True,
    adapter: CliRemoteGitAdapter | None = None,
    provider: Any = None,
) -> dict[str, Any]:
    project = validate_project_name(project)
    root = get_project_path(project)
    adapter = adapter or CliRemoteGitAdapter()
    handle = acquire(
        project_lock_path(project),
        command="release-create",
        project=project,
    )
    conn = open_project_db(project, init=True)
    try:
        try:
            plan = run_release_precheck(
                conn,
                project,
                root,
                verification_record_id=verification,
                adapter=adapter,
                fetch=fetch,
            )
        except (PrecheckBlocked, PrecheckRetryable, PrecheckManual) as exc:
            return {
                "project": project,
                "dry_run": not execute,
                "execute": execute,
                "ok_to_execute": False,
                "error_kind": exc.kind,
                "error": str(exc),
                "details": getattr(exc, "details", None),
                "write_performed": False,
            }

        if not execute:
            return {
                "project": project,
                "dry_run": True,
                "execute": False,
                "ok_to_execute": True,
                "plan": {
                    "head": plan["develop"],
                    "base": plan["master"],
                    "source_sha": plan["source_sha"],
                    "target_sha_before": plan["target_sha_before"],
                    "repository": plan["repository"],
                },
                "write_performed": False,
            }

        host = provider or get_release_provider(project)
        if host is None:
            raise ValidationError(
                "GitHub credentials required for release-create "
                "(ORCH_GITHUB_TOKEN or App env)",
                kind="promotion_provider_missing",
            )

        with immediate_transaction(conn):
            run = promo_repo.create_run(
                conn,
                project_name=project,
                kind="master_release",
                mode="promotion_pr",
                remote_name=plan["remote"],
                provider=plan["provider"],
                source_ref=f"refs/heads/{plan['develop']}",
                target_ref=f"refs/heads/{plan['master']}",
                source_sha=plan["source_sha"],
                target_sha_before=plan["target_sha_before"],
                created_by="operator",
                state="created",
                verification_record_id=verification,
            )
            run = _transition(
                conn,
                run,
                "prechecking",
                event_type="release_precheck_started",
                source="release-create",
            )

        pr_title = title or f"orch release: {plan['develop']} → {plan['master']}"
        body = (
            f"<!-- orch-promotion-id: {run['id']} -->\n"
            f"orch-promotion-id: `{run['id']}`\n"
            f"source-sha: `{plan['source_sha']}`\n"
            f"target-sha-before: `{plan['target_sha_before']}`\n"
            f"verification: `{verification}`\n"
            f"\nCreated by orch release-create. Do not squash/rebase.\n"
        )
        pr = host.create_promotion_pr(
            plan["develop"],
            plan["master"],
            pr_title,
            body,
        )
        if pr.get("kind") and pr.get("kind") not in (None, ""):
            with immediate_transaction(conn):
                run = promo_repo.get_run(conn, run["id"])
                assert run is not None
                run = _transition(
                    conn,
                    run,
                    "blocked",
                    event_type="release_pr_failed",
                    source="release-create",
                    detail={"kind": pr.get("kind"), "detail": pr.get("detail")},
                    last_error=str(pr.get("detail") or pr.get("kind"))[:400],
                )
            return {
                "project": project,
                "promotion": run,
                "pr": pr,
                "ok": False,
                "write_performed": True,
            }

        # head 滑移：创建后 head_sha 必须匹配冻结 source
        if pr.get("head_sha") and pr["head_sha"] != plan["source_sha"]:
            with immediate_transaction(conn):
                run = promo_repo.get_run(conn, run["id"])
                assert run is not None
                run = _transition(
                    conn,
                    run,
                    "blocked",
                    event_type="release_head_mismatch",
                    source="release-create",
                    detail={
                        "expected": plan["source_sha"],
                        "head_sha": pr.get("head_sha"),
                    },
                    external_id=str(pr.get("external_id") or ""),
                    external_url=str(pr.get("url") or ""),
                    last_error="PR head_sha != frozen source_sha",
                )
            return {
                "project": project,
                "promotion": run,
                "pr": {k: pr[k] for k in pr if k != "kind" or True},
                "ok": False,
                "write_performed": True,
            }

        with immediate_transaction(conn):
            run = promo_repo.get_run(conn, run["id"])
            assert run is not None
            run = _transition(
                conn,
                run,
                "awaiting_checks",
                event_type="release_pr_created",
                source="release-create",
                detail={
                    "external_id": pr.get("external_id"),
                    "url": pr.get("url"),
                },
                external_id=str(pr.get("external_id") or ""),
                external_url=str(pr.get("url") or ""),
            )

        return {
            "project": project,
            "promotion": run,
            "pr": {
                "external_id": pr.get("external_id"),
                "url": pr.get("url"),
                "head": pr.get("head"),
                "base": pr.get("base"),
                "head_sha": pr.get("head_sha"),
            },
            "ok": True,
            "write_performed": True,
            "note": "does not approve or merge",
        }
    finally:
        conn.close()
        release(handle)


def release_status(
    project: str,
    promotion_id: str,
    *,
    fetch: bool = True,
    adapter: CliRemoteGitAdapter | None = None,
    provider: Any = None,
) -> dict[str, Any]:
    project = validate_project_name(project)
    root = get_project_path(project)
    adapter = adapter or CliRemoteGitAdapter()
    handle = acquire(
        project_lock_path(project),
        command="release-status",
        project=project,
    )
    conn = open_project_db(project, init=True)
    try:
        run = promo_repo.get_run(conn, promotion_id)
        if run is None or run.get("project_name") != project:
            raise ValidationError(
                f"promotion not found: {promotion_id}",
                kind="promotion_not_found",
                details={"id": promotion_id},
            )
        if run["kind"] != "master_release":
            raise ValidationError(
                "release-status requires kind=master_release",
                kind="promotion_unsupported",
                details={"kind": run["kind"]},
            )

        from orch.promotion.config import get_promotion_config

        promo = get_promotion_config(project) or {}
        remote = str(run.get("remote_name") or promo.get("remote") or "origin")
        develop = str(promo.get("integration_branch") or "develop")
        master = str(promo.get("stable_branch") or "master")
        bare = root / BARE_DIR_NAME
        if fetch and bare.is_dir():
            try:
                adapter.fetch_core_refs(bare, remote, develop, master)
            except Exception:  # noqa: BLE001
                pass

        master_tip = adapter._live_remote_tip(bare, remote, master) if bare.is_dir() else None
        host = provider or get_release_provider(project)
        if host is None or not run.get("external_id"):
            return {
                "project": project,
                "promotion": run,
                "master_tip": master_tip,
                "provider": None,
                "note": "provider credentials or external_id missing",
                "write_performed": False,
            }

        pr = host.get_pr(str(run["external_id"]))
        source_sha = run["source_sha"]
        head_sha = pr.get("head_sha") or ""
        checks = host.get_checks(str(run["external_id"]), source_sha)
        reviews = host.get_reviews(str(run["external_id"]), source_sha)
        required = list(promo.get("required_checks") or [])
        required_approvals = int(promo.get("required_approvals") or 1)

        slipped = bool(head_sha) and head_sha.lower() != source_sha.lower()
        base_ok = (pr.get("base") or "") == master
        head_ok = (pr.get("head") or "") == develop

        observation = {
            "pr_state": pr.get("state"),
            "merged": bool(pr.get("merged")),
            "merge_commit_sha": pr.get("merge_commit_sha"),
            "mergeable": pr.get("mergeable"),
            "head_sha": head_sha,
            "source_sha": source_sha,
            "head_slipped": slipped,
            "base_ok": base_ok,
            "head_ok": head_ok,
            "checks": checks.get("checks") or [],
            "approved_bound_human_count": reviews.get("approved_bound_human_count", 0),
            "required_approvals": required_approvals,
            "master_tip": master_tip,
            "kind": pr.get("kind"),
        }

        with immediate_transaction(conn):
            run = promo_repo.get_run(conn, promotion_id)
            assert run is not None
            if run["state"] in ("released", "cancelled"):
                return {
                    "project": project,
                    "promotion": run,
                    "observation": observation,
                    "write_performed": False,
                }

            if slipped or not base_ok or not head_ok or pr.get("state") == "closed" and not pr.get(
                "merged"
            ):
                if run["state"] not in ("blocked", "manual_required"):
                    run = _transition(
                        conn,
                        run,
                        "blocked",
                        event_type="release_blocked_slip",
                        source="release-status",
                        detail=observation,
                        last_error="PR head/base slipped or closed without merge",
                        observed_target_sha=master_tip,
                    )
            elif pr.get("merged"):
                if pr.get("kind") == "merge_not_syncable":
                    run = _transition(
                        conn,
                        run,
                        "blocked",
                        event_type="release_merge_not_syncable",
                        source="release-status",
                        detail=observation,
                        last_error="squash/rebase merge not syncable",
                        published_sha=pr.get("merge_commit_sha"),
                        observed_target_sha=master_tip,
                    )
                elif run["state"] != "master_merged_pending_sync":
                    # advance through ready_to_merge if needed
                    if run["state"] in ("awaiting_checks", "awaiting_approval"):
                        run = _transition(
                            conn,
                            run,
                            "ready_to_merge",
                            event_type="release_ready",
                            source="release-status",
                            detail={"reason": "provider_merged"},
                        )
                    if run["state"] == "ready_to_merge":
                        run = _transition(
                            conn,
                            run,
                            "master_merged_pending_sync",
                            event_type="release_master_merged",
                            source="release-status",
                            detail=observation,
                            published_sha=pr.get("merge_commit_sha"),
                            observed_target_sha=master_tip,
                        )
                    elif run["state"] not in (
                        "master_merged_pending_sync",
                        "syncing",
                        "reconciling",
                    ):
                        # from blocked/reconciling etc. — enter pending_sync via reconciling path
                        if run["state"] == "blocked":
                            run = _transition(
                                conn,
                                run,
                                "reconciling",
                                event_type="release_reconcile",
                                source="release-status",
                            )
                        if run["state"] == "reconciling":
                            run = _transition(
                                conn,
                                run,
                                "master_merged_pending_sync",
                                event_type="release_master_merged",
                                source="release-status",
                                detail=observation,
                                published_sha=pr.get("merge_commit_sha"),
                                observed_target_sha=master_tip,
                            )
            else:
                # open PR — check gates
                bound_names = {
                    c.get("name")
                    for c in (checks.get("checks") or [])
                    if c.get("bound_to_source") and c.get("conclusion") == "success"
                }
                checks_ok = all(name in bound_names for name in required) if required else True
                approvals_ok = int(reviews.get("approved_bound_human_count") or 0) >= required_approvals
                if checks_ok and run["state"] == "awaiting_checks":
                    run = _transition(
                        conn,
                        run,
                        "awaiting_approval",
                        event_type="release_checks_passed",
                        source="release-status",
                        detail={"bound_passed": sorted(bound_names)},
                    )
                if approvals_ok and run["state"] == "awaiting_approval":
                    run = _transition(
                        conn,
                        run,
                        "ready_to_merge",
                        event_type="release_approvals_satisfied",
                        source="release-status",
                        detail={"approvals": reviews.get("approved_bound_human_count")},
                    )
                # missing gates stay put — never ready_to_merge without both
                if run["state"] == "ready_to_merge" and (not checks_ok or not approvals_ok):
                    run = _transition(
                        conn,
                        run,
                        "blocked",
                        event_type="release_gates_regressed",
                        source="release-status",
                        detail=observation,
                        last_error="checks/approvals no longer satisfied",
                    )

        return {
            "project": project,
            "promotion": run,
            "observation": observation,
            "write_performed": True,
            "note": "merged → master_merged_pending_sync; not released until release-sync",
        }
    finally:
        conn.close()
        release(handle)
