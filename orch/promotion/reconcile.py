"""promotion-reconcile / cancel / list / show（V13-008/011）。"""

from __future__ import annotations

from typing import Any

from orch.constants import BARE_DIR_NAME, project_lock_path
from orch.db import immediate_transaction, open_project_db
from orch.errors import ValidationError
from orch.locks import acquire, release
from orch.promotion import repo as promo_repo
from orch.promotion.config import get_promotion_config
from orch.promotion.state import (
    MASTER_CANCELABLE,
    assert_develop_transition,
    assert_master_transition,
)
from orch.registry import get_project_path
from orch.remote.git import CliRemoteGitAdapter
from orch.util import utc_now_iso
from orch.validate import validate_project_name

_DEVELOP_CANCELABLE = frozenset(
    {
        "created",
        "prechecking",
        "ready",
        "failed_safe_to_retry",
        "blocked",
        "manual_required",
        "reconciling",
    }
)


def _transition_develop(
    conn: Any,
    run: dict[str, Any],
    to_state: str,
    *,
    event_type: str,
    source: str,
    detail: Any = None,
    **fields: Any,
) -> dict[str, Any]:
    assert_develop_transition(run["state"], to_state)
    payload = {"state": to_state, **fields}
    if to_state in ("succeeded", "cancelled", "released"):
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


def _transition_master(
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


# 兼容 V13-008 测试与旧调用
_transition = _transition_develop


def promotion_list(
    project: str,
    *,
    kind: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    project = validate_project_name(project)
    conn = open_project_db(project, init=True)
    try:
        rows = promo_repo.list_runs(conn, project, kind=kind, limit=limit)
        return {
            "project": project,
            "count": len(rows),
            "promotions": rows,
            "write_performed": False,
        }
    finally:
        conn.close()


def promotion_show(project: str, promotion_id: str) -> dict[str, Any]:
    project = validate_project_name(project)
    conn = open_project_db(project, init=True)
    try:
        run = promo_repo.get_run(conn, promotion_id)
        if run is None or run.get("project_name") != project:
            raise ValidationError(
                f"promotion not found: {promotion_id}",
                kind="promotion_not_found",
                details={"id": promotion_id},
            )
        return {
            "project": project,
            "promotion": run,
            "events": promo_repo.list_events(conn, promotion_id),
            "tasks": promo_repo.list_tasks(conn, promotion_id),
            "write_performed": False,
        }
    finally:
        conn.close()


def promotion_reconcile(
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
        command="promotion-reconcile",
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
        if run["kind"] == "master_release":
            # release lock before nested commands that re-acquire
            conn.close()
            release(handle)
            handle = None
            return _reconcile_master_unlocked(
                project,
                run["id"],
                fetch=fetch,
                adapter=adapter,
                provider=provider,
            )
        if run["kind"] != "develop_publish":
            raise ValidationError(
                f"unsupported promotion kind: {run['kind']}",
                kind="promotion_unsupported",
                details={"kind": run["kind"]},
            )
        if run["mode"] == "candidate_pr":
            raise ValidationError(
                "candidate-sync not implemented while mode is reserved; use direct_ff path",
                kind="promotion_unsupported",
                details={"mode": run["mode"]},
            )
        return _reconcile_develop(
            conn, project, root, run, fetch=fetch, adapter=adapter
        )
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        if handle is not None:
            release(handle)


def _reconcile_master_unlocked(
    project: str,
    promotion_id: str,
    *,
    fetch: bool,
    adapter: CliRemoteGitAdapter,
    provider: Any,
) -> dict[str, Any]:
    from orch.promotion.release_service import release_status
    from orch.promotion.release_sync import release_sync

    status = release_status(
        project,
        promotion_id,
        fetch=fetch,
        adapter=adapter,
        provider=provider,
    )
    run2 = status.get("promotion") or {}
    extra: dict[str, Any] = {}
    if run2.get("state") in ("master_merged_pending_sync", "syncing", "reconciling"):
        extra["sync_preview"] = release_sync(
            project,
            promotion_id,
            execute=False,
            fetch=False,
            adapter=adapter,
            provider=provider,
        )
    return {
        "project": project,
        "promotion": run2,
        "reconcile": "master_status",
        "observation": status.get("observation"),
        **extra,
        "write_performed": bool(status.get("write_performed")),
    }


def _reconcile_develop(
    conn: Any,
    project: str,
    root: Any,
    run: dict[str, Any],
    *,
    fetch: bool,
    adapter: CliRemoteGitAdapter,
) -> dict[str, Any]:
    promotion_id = run["id"]
    if run["state"] in ("succeeded", "cancelled", "released"):
        return {
            "project": project,
            "promotion": run,
            "reconcile": "noop_terminal",
            "write_performed": False,
        }

    promo = get_promotion_config(project) or {}
    remote = str(run.get("remote_name") or promo.get("remote") or "origin")
    develop = "develop"
    bare = root / BARE_DIR_NAME
    if fetch:
        try:
            adapter.fetch_core_refs(
                bare,
                remote,
                develop,
                str(promo.get("stable_branch") or "master"),
            )
        except Exception as exc:  # noqa: BLE001
            with immediate_transaction(conn):
                run = _enter_reconciling_develop(conn, run, reason=f"fetch_failed:{exc}")
            return {
                "project": project,
                "promotion": run,
                "reconcile": "fetch_failed",
                "error": str(exc)[:400],
                "write_performed": False,
            }

    tip = adapter._live_remote_tip(bare, remote, develop)
    source_sha = run["source_sha"]
    old_sha = run["target_sha_before"]

    with immediate_transaction(conn):
        run = promo_repo.get_run(conn, promotion_id)
        assert run is not None
        if run["state"] not in (
            "reconciling",
            "executing",
            "blocked",
            "ready",
            "failed_safe_to_retry",
            "manual_required",
        ):
            run = _enter_reconciling_develop(conn, run, reason="operator_reconcile")

        if tip == source_sha:
            if run["state"] != "succeeded":
                if run["state"] != "reconciling":
                    run = _enter_reconciling_develop(conn, run, reason="tip_matches_source")
                run = _transition_develop(
                    conn,
                    run,
                    "succeeded",
                    event_type="reconcile_succeeded",
                    source="promotion-reconcile",
                    detail={"observed_tip": tip},
                    published_sha=source_sha,
                    observed_target_sha=tip,
                    last_error=None,
                )
            outcome = "succeeded"
        elif tip == old_sha:
            if run["state"] != "reconciling":
                run = _enter_reconciling_develop(conn, run, reason="tip_matches_old")
            run = _transition_develop(
                conn,
                run,
                "failed_safe_to_retry",
                event_type="reconcile_old_sha",
                source="promotion-reconcile",
                detail={"observed_tip": tip},
                observed_target_sha=tip,
            )
            outcome = "failed_safe_to_retry"
        else:
            if run["state"] != "reconciling":
                run = _enter_reconciling_develop(conn, run, reason="tip_third_sha")
            run = _transition_develop(
                conn,
                run,
                "manual_required",
                event_type="reconcile_third_sha",
                source="promotion-reconcile",
                detail={
                    "observed_tip": tip,
                    "expected_new": source_sha,
                    "expected_old": old_sha,
                },
                observed_target_sha=tip,
                last_error="remote tip is neither source_sha nor target_sha_before",
            )
            outcome = "manual_required"

    return {
        "project": project,
        "promotion": run,
        "observed_tip": tip,
        "reconcile": outcome,
        "write_performed": False,
    }


def _enter_reconciling_develop(
    conn: Any, run: dict[str, Any], *, reason: str
) -> dict[str, Any]:
    if run["state"] == "reconciling":
        promo_repo.append_event(
            conn,
            promotion_id=run["id"],
            event_type="reconcile_started",
            source="promotion-reconcile",
            detail={"reason": reason},
        )
        return run
    return _transition_develop(
        conn,
        run,
        "reconciling",
        event_type="reconcile_started",
        source="promotion-reconcile",
        detail={"reason": reason},
    )


def promotion_cancel(
    project: str,
    promotion_id: str,
    *,
    reason: str,
    actor: str = "operator",
    fetch: bool = True,
    adapter: CliRemoteGitAdapter | None = None,
) -> dict[str, Any]:
    project = validate_project_name(project)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError(
            "--reason is required for promotion-cancel",
            kind="promotion_usage",
            details={"flag": "--reason"},
        )
    root = get_project_path(project)
    adapter = adapter or CliRemoteGitAdapter()
    handle = acquire(
        project_lock_path(project),
        command="promotion-cancel",
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
        if run["state"] in ("succeeded", "released", "cancelled"):
            raise ValidationError(
                f"cannot cancel promotion in state {run['state']!r}",
                kind="promotion_cancel_refused",
                details={"state": run["state"]},
            )

        if run["kind"] == "master_release":
            return _cancel_master(
                conn,
                project,
                root,
                run,
                reason=reason,
                actor=actor,
                fetch=fetch,
                adapter=adapter,
            )

        if run["state"] not in _DEVELOP_CANCELABLE:
            raise ValidationError(
                f"cannot cancel promotion in state {run['state']!r} (may have written remote)",
                kind="promotion_cancel_refused",
                details={"state": run["state"]},
            )

        promo = get_promotion_config(project) or {}
        remote = str(run.get("remote_name") or promo.get("remote") or "origin")
        bare = root / BARE_DIR_NAME
        tip = None
        if fetch and bare.is_dir():
            try:
                adapter.fetch_core_refs(
                    bare,
                    remote,
                    "develop",
                    str(promo.get("stable_branch") or "master"),
                )
            except Exception:  # noqa: BLE001
                pass
            tip = adapter._live_remote_tip(bare, remote, "develop")

        if tip and tip == run["source_sha"]:
            raise ValidationError(
                "remote already at source_sha; reconcile to succeeded instead of cancel",
                kind="promotion_cancel_refused",
                details={"observed_tip": tip, "source_sha": run["source_sha"]},
            )

        with immediate_transaction(conn):
            run = promo_repo.get_run(conn, promotion_id)
            assert run is not None
            if run["state"] != "cancelled":
                if run["state"] not in _DEVELOP_CANCELABLE:
                    raise ValidationError(
                        f"cannot cancel promotion in state {run['state']!r}",
                        kind="promotion_cancel_refused",
                        details={"state": run["state"]},
                    )
                run = _transition_develop(
                    conn,
                    run,
                    "cancelled",
                    event_type="cancelled",
                    source="promotion-cancel",
                    detail={
                        "actor": actor,
                        "reason": reason,
                        "observed_tip": tip,
                    },
                    last_error=f"cancelled by {actor}: {reason}"[:400],
                    observed_target_sha=tip,
                )

        return {
            "project": project,
            "promotion": run,
            "cancelled": True,
            "reason": reason,
            "actor": actor,
            "observed_tip": tip,
            "write_performed": False,
        }
    finally:
        conn.close()
        release(handle)


def _cancel_master(
    conn: Any,
    project: str,
    root: Any,
    run: dict[str, Any],
    *,
    reason: str,
    actor: str,
    fetch: bool,
    adapter: CliRemoteGitAdapter,
) -> dict[str, Any]:
    if run["state"] in ("master_merged_pending_sync", "syncing", "released"):
        raise ValidationError(
            f"cannot cancel master_release in state {run['state']!r}; "
            "use release-sync or reconcile → manual_required first",
            kind="promotion_cancel_refused",
            details={"state": run["state"]},
        )
    if run["state"] not in MASTER_CANCELABLE:
        raise ValidationError(
            f"cannot cancel master_release in state {run['state']!r}; "
            f"allowed={sorted(MASTER_CANCELABLE)}",
            kind="promotion_cancel_refused",
            details={"state": run["state"]},
        )

    promo = get_promotion_config(project) or {}
    remote = str(run.get("remote_name") or promo.get("remote") or "origin")
    bare = root / BARE_DIR_NAME
    master_tip = None
    if fetch and bare.is_dir():
        try:
            adapter.fetch_core_refs(
                bare,
                remote,
                str(promo.get("integration_branch") or "develop"),
                str(promo.get("stable_branch") or "master"),
            )
        except Exception:  # noqa: BLE001
            pass
        master_tip = adapter._live_remote_tip(
            bare, remote, str(promo.get("stable_branch") or "master")
        )

    with immediate_transaction(conn):
        run = promo_repo.get_run(conn, run["id"])
        assert run is not None
        run = _transition_master(
            conn,
            run,
            "cancelled",
            event_type="cancelled",
            source="promotion-cancel",
            detail={
                "actor": actor,
                "reason": reason,
                "observed_master_tip": master_tip,
                "note": "cancel does not hide prior master writes if any",
            },
            last_error=f"cancelled by {actor}: {reason}"[:400],
            observed_target_sha=master_tip,
        )

    return {
        "project": project,
        "promotion": run,
        "cancelled": True,
        "reason": reason,
        "actor": actor,
        "observed_master_tip": master_tip,
        "write_performed": False,
    }
