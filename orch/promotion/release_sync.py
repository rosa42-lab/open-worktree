"""release-sync（V13-011 / 设计 §8.7）。"""

from __future__ import annotations

from typing import Any

from orch.constants import BARE_DIR_NAME, project_lock_path
from orch.db import immediate_transaction, open_project_db
from orch.errors import OrchError, ValidationError
from orch.locks import acquire, release
from orch.promotion import repo as promo_repo
from orch.promotion.auth_provider import get_release_provider
from orch.promotion.config import get_promotion_config
from orch.promotion.state import assert_master_transition
from orch.registry import get_project_path
from orch.remote.git import CliRemoteGitAdapter
from orch.util import utc_now_iso
from orch.validate import validate_project_name

_SYNCABLE = frozenset(
    {"master_merged_pending_sync", "syncing", "reconciling"}
)


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


def release_sync(
    project: str,
    promotion_id: str,
    *,
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
        command="release-sync",
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
                "release-sync requires kind=master_release",
                kind="promotion_unsupported",
                details={"kind": run["kind"]},
            )
        if run["state"] not in _SYNCABLE and run["state"] != "released":
            raise ValidationError(
                f"release-sync not allowed in state {run['state']!r}",
                kind="promotion_sync_refused",
                details={"state": run["state"], "allowed": sorted(_SYNCABLE)},
            )
        if run["state"] == "released":
            return {
                "project": project,
                "promotion": run,
                "sync": "already_released",
                "write_performed": False,
            }

        promo = get_promotion_config(project) or {}
        remote = str(run.get("remote_name") or promo.get("remote") or "origin")
        develop = str(promo.get("integration_branch") or "develop")
        master = str(promo.get("stable_branch") or "master")
        bare = root / BARE_DIR_NAME
        if not bare.is_dir():
            raise ValidationError(".bare.git missing", kind="bare_missing")

        if fetch:
            adapter.fetch_core_refs(bare, remote, develop, master)

        host = provider or get_release_provider(project)
        merge_sha = run.get("published_sha")
        if host is not None and run.get("external_id"):
            pr = host.get_pr(str(run["external_id"]))
            if pr.get("kind") == "merge_not_syncable":
                with immediate_transaction(conn):
                    run = promo_repo.get_run(conn, promotion_id)
                    assert run is not None
                    if run["state"] != "blocked":
                        if run["state"] == "master_merged_pending_sync":
                            run = _transition(
                                conn,
                                run,
                                "blocked",
                                event_type="release_sync_not_syncable",
                                source="release-sync",
                                detail={"kind": pr.get("kind")},
                                last_error="merge_not_syncable",
                            )
                        else:
                            run = _transition(
                                conn,
                                run,
                                "manual_required",
                                event_type="release_sync_not_syncable",
                                source="release-sync",
                                detail={"kind": pr.get("kind")},
                                last_error="merge_not_syncable",
                            )
                return {
                    "project": project,
                    "promotion": run,
                    "sync": "blocked_not_syncable",
                    "write_performed": True,
                }
            if pr.get("merge_commit_sha"):
                merge_sha = pr["merge_commit_sha"]

        if not merge_sha:
            raise ValidationError(
                "merge_commit_sha unknown; run release-status after platform merge",
                kind="promotion_sync_refused",
            )

        master_tip = adapter._live_remote_tip(bare, remote, master)
        source_sha = run["source_sha"]
        local_develop = adapter.local_head(bare, develop)
        remote_develop = adapter._live_remote_tip(bare, remote, develop)

        plan = {
            "source_sha": source_sha,
            "merge_sha": merge_sha,
            "master_tip": master_tip,
            "local_develop": local_develop,
            "remote_develop": remote_develop,
            "expected_old_for_cas": remote_develop,
        }

        # validations
        errors: list[str] = []
        if master_tip != merge_sha:
            errors.append("master tip != merge_commit_sha")
        if not adapter.is_ancestor(bare, source_sha, merge_sha):
            errors.append("source_sha is not ancestor of merge_sha")
        if local_develop not in (source_sha, merge_sha):
            errors.append("local develop must be source_sha or merge_sha")

        if errors:
            with immediate_transaction(conn):
                run = promo_repo.get_run(conn, promotion_id)
                assert run is not None
                target = "manual_required" if "ancestor" in " ".join(errors) else "blocked"
                if run["state"] == "syncing":
                    run = _transition(
                        conn,
                        run,
                        target,
                        event_type="release_sync_failed",
                        source="release-sync",
                        detail={"errors": errors, **plan},
                        last_error="; ".join(errors)[:400],
                    )
                elif run["state"] == "master_merged_pending_sync":
                    run = _transition(
                        conn,
                        run,
                        target,
                        event_type="release_sync_failed",
                        source="release-sync",
                        detail={"errors": errors, **plan},
                        last_error="; ".join(errors)[:400],
                    )
                elif run["state"] == "reconciling":
                    run = _transition(
                        conn,
                        run,
                        target,
                        event_type="release_sync_failed",
                        source="release-sync",
                        detail={"errors": errors, **plan},
                        last_error="; ".join(errors)[:400],
                    )
            return {
                "project": project,
                "promotion": run,
                "plan": plan,
                "errors": errors,
                "sync": "failed",
                "write_performed": True,
            }

        if not execute:
            return {
                "project": project,
                "promotion": run,
                "plan": plan,
                "dry_run": True,
                "ok_to_execute": True,
                "write_performed": False,
            }

        with immediate_transaction(conn):
            run = promo_repo.get_run(conn, promotion_id)
            assert run is not None
            if run["state"] == "master_merged_pending_sync":
                run = _transition(
                    conn,
                    run,
                    "syncing",
                    event_type="release_sync_started",
                    source="release-sync",
                    detail=plan,
                    published_sha=merge_sha,
                )
            elif run["state"] == "reconciling":
                run = _transition(
                    conn,
                    run,
                    "syncing",
                    event_type="release_sync_started",
                    source="release-sync",
                    detail=plan,
                    published_sha=merge_sha,
                )
            promo_repo.append_event(
                conn,
                promotion_id=run["id"],
                event_type="release_sync_started",
                source="release-sync",
                detail=plan,
            )

        # Git outside transaction
        try:
            adapter.sync_verified_merge(bare, source_sha, merge_sha)
            # CAS push origin/develop: expected_old = remote tip before sync
            # After local sync, local develop == merge_sha; remote should still be source or merge
            expected_old = remote_develop or source_sha
            if expected_old != merge_sha:
                adapter.push_fast_forward(
                    bare,
                    remote,
                    "refs/heads/develop",
                    "refs/heads/develop",
                    expected_old_sha=expected_old,
                    new_sha=merge_sha,
                )
        except OrchError as exc:
            with immediate_transaction(conn):
                run = promo_repo.get_run(conn, promotion_id)
                assert run is not None
                run = _transition(
                    conn,
                    run,
                    "reconciling",
                    event_type="release_sync_error",
                    source="release-sync",
                    detail={"kind": getattr(exc, "kind", None), "error": str(exc)[:400]},
                    last_error=str(exc)[:400],
                )
            return {
                "project": project,
                "promotion": run,
                "sync": "reconciling",
                "error": str(exc),
                "write_performed": True,
            }

        # dual ref check
        local2 = adapter.local_head(bare, develop)
        remote2 = adapter._live_remote_tip(bare, remote, develop)
        master2 = adapter._live_remote_tip(bare, remote, master)
        if local2 != merge_sha or remote2 != merge_sha or master2 != merge_sha:
            with immediate_transaction(conn):
                run = promo_repo.get_run(conn, promotion_id)
                assert run is not None
                run = _transition(
                    conn,
                    run,
                    "manual_required",
                    event_type="release_sync_postcheck_failed",
                    source="release-sync",
                    detail={
                        "local": local2,
                        "remote_develop": remote2,
                        "master": master2,
                        "expected": merge_sha,
                    },
                    last_error="post-sync dual ref mismatch",
                    observed_target_sha=remote2,
                )
            return {
                "project": project,
                "promotion": run,
                "sync": "manual_required",
                "write_performed": True,
            }

        with immediate_transaction(conn):
            run = promo_repo.get_run(conn, promotion_id)
            assert run is not None
            run = _transition(
                conn,
                run,
                "released",
                event_type="release_sync_published",
                source="release-sync",
                detail={"merge_sha": merge_sha},
                published_sha=merge_sha,
                observed_target_sha=remote2,
                last_error=None,
            )
            promo_repo.append_event(
                conn,
                promotion_id=run["id"],
                event_type="release_sync_reconciled",
                source="release-sync",
                detail={"local": local2, "remote": remote2, "master": master2},
            )

        return {
            "project": project,
            "promotion": run,
            "sync": "released",
            "merge_sha": merge_sha,
            "write_performed": True,
        }
    finally:
        conn.close()
        release(handle)
