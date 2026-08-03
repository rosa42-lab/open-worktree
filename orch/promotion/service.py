"""PromotionService：promote-develop（V13-007 / 设计 §9）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from orch.constants import BARE_DIR_NAME, project_lock_path
from orch.db import immediate_transaction, open_project_db
from orch.errors import ExitCode, OrchError, ValidationError
from orch.locks import acquire, release
from orch.promotion import repo as promo_repo
from orch.promotion.precheck import (
    PrecheckBlocked,
    PrecheckManual,
    PrecheckRetryable,
    run_develop_precheck,
)
from orch.promotion.state import assert_develop_transition
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


def promote_develop(
    project: str,
    *,
    execute: bool = False,
    verification_record_id: str | None = None,
    fetch: bool = True,
    adapter: CliRemoteGitAdapter | None = None,
) -> dict[str, Any]:
    project = validate_project_name(project)
    if execute and not verification_record_id:
        raise ValidationError(
            "--verification is required with --execute",
            kind="promotion_usage",
            details={"flag": "--verification"},
        )

    root = get_project_path(project)
    adapter = adapter or CliRemoteGitAdapter()
    handle = acquire(
        project_lock_path(project),
        command="promote-develop",
        project=project,
    )
    conn = open_project_db(project, init=True)
    try:
        # dry-run：只做 precheck，不占 active 槽位
        if not execute:
            try:
                plan = run_develop_precheck(
                    conn,
                    project,
                    root,
                    verification_record_id=verification_record_id,
                    adapter=adapter,
                    fetch=fetch,
                )
            except (PrecheckBlocked, PrecheckRetryable, PrecheckManual) as exc:
                return {
                    "project": project,
                    "dry_run": True,
                    "execute": False,
                    "ok_to_execute": False,
                    "error_kind": exc.kind,
                    "error": str(exc),
                    "details": getattr(exc, "details", None),
                    "write_performed": False,
                }
            return {
                "project": project,
                "dry_run": True,
                "execute": False,
                "ok_to_execute": True,
                "plan": plan,
                "write_performed": False,
            }

        # --- execute path ---
        try:
            plan = run_develop_precheck(
                conn,
                project,
                root,
                verification_record_id=verification_record_id,
                adapter=adapter,
                fetch=fetch,
            )
        except PrecheckRetryable as exc:
            with immediate_transaction(conn):
                run = promo_repo.create_run(
                    conn,
                    project_name=project,
                    kind="develop_publish",
                    mode="direct_ff",
                    remote_name="origin",
                    provider="github",
                    source_ref="refs/heads/develop",
                    target_ref="refs/heads/develop",
                    source_sha="unknown",
                    target_sha_before="unknown",
                    created_by="orch",
                    state="created",
                    verification_record_id=verification_record_id,
                )
                run = _transition(
                    conn,
                    run,
                    "prechecking",
                    event_type="precheck_started",
                    source="promote-develop",
                )
                run = _transition(
                    conn,
                    run,
                    "failed_safe_to_retry",
                    event_type="precheck_failed",
                    source="promote-develop",
                    detail={"error": str(exc), "kind": exc.kind},
                    last_error=str(exc)[:400],
                )
            return {
                "project": project,
                "dry_run": False,
                "execute": True,
                "promotion": run,
                "write_performed": False,
                "error_kind": exc.kind,
                "error": str(exc),
            }
        except PrecheckManual as exc:
            with immediate_transaction(conn):
                run = promo_repo.create_run(
                    conn,
                    project_name=project,
                    kind="develop_publish",
                    mode="direct_ff",
                    remote_name="origin",
                    provider="github",
                    source_ref="refs/heads/develop",
                    target_ref="refs/heads/develop",
                    source_sha="unknown",
                    target_sha_before="unknown",
                    created_by="orch",
                    state="created",
                    verification_record_id=verification_record_id,
                )
                run = _transition(
                    conn, run, "prechecking", event_type="precheck_started", source="promote-develop"
                )
                run = _transition(
                    conn,
                    run,
                    "manual_required",
                    event_type="precheck_manual",
                    source="promote-develop",
                    detail={"error": str(exc)},
                    last_error=str(exc)[:400],
                )
            return {
                "project": project,
                "dry_run": False,
                "execute": True,
                "promotion": run,
                "write_performed": False,
                "error_kind": exc.kind,
                "error": str(exc),
            }
        except PrecheckBlocked as exc:
            details = getattr(exc, "details", None) or {}
            sha = details.get("sha")
            if sha:
                with immediate_transaction(conn):
                    existing = conn.execute(
                        """
                        SELECT * FROM promotion_runs
                        WHERE project_name = ? AND kind = 'develop_publish'
                          AND source_sha = ? AND state = 'succeeded'
                        ORDER BY created_at DESC LIMIT 1
                        """,
                        (project, sha),
                    ).fetchone()
                if existing is not None:
                    return {
                        "project": project,
                        "dry_run": False,
                        "execute": True,
                        "idempotent": True,
                        "promotion": dict(existing),
                        "write_performed": False,
                    }
            return {
                "project": project,
                "dry_run": False,
                "execute": True,
                "ok_to_execute": False,
                "write_performed": False,
                "error_kind": exc.kind,
                "error": str(exc),
                "details": details,
            }

        source_sha = plan["source_sha"]
        old_sha = plan["target_sha_before"]

        # 幂等：远端已是 source_sha（竞态窗口外）或已有成功记录
        with immediate_transaction(conn):
            existing = conn.execute(
                """
                SELECT * FROM promotion_runs
                WHERE project_name = ? AND kind = 'develop_publish'
                  AND source_sha = ? AND state = 'succeeded'
                ORDER BY created_at DESC LIMIT 1
                """,
                (project, source_sha),
            ).fetchone()
            if existing is not None:
                return {
                    "project": project,
                    "dry_run": False,
                    "execute": True,
                    "idempotent": True,
                    "promotion": dict(existing),
                    "write_performed": False,
                }

            active = promo_repo.find_active(conn, project, "develop_publish")
            if active is not None:
                raise ValidationError(
                    "active develop_publish already exists",
                    kind="promotion_conflict",
                    details={"promotion_id": active["id"], "state": active["state"]},
                )

            run = promo_repo.create_run(
                conn,
                project_name=project,
                kind="develop_publish",
                mode=plan["mode"],
                remote_name=plan["remote"],
                provider=plan["provider"],
                source_ref=plan["source_ref"],
                target_ref=plan["target_ref"],
                source_sha=source_sha,
                target_sha_before=old_sha,
                created_by="orch",
                state="created",
                verification_record_id=plan["verification_record_id"],
            )
            run = _transition(
                conn, run, "prechecking", event_type="precheck_started", source="promote-develop"
            )
            run = _transition(
                conn,
                run,
                "ready",
                event_type="precheck_passed",
                source="promote-develop",
                detail={
                    "source_sha": source_sha,
                    "target_sha_before": old_sha,
                    "tasks": plan["included_tasks"],
                },
            )
            links = [
                (t["task_id"], t["merged_commit"]) for t in plan.get("included_tasks") or []
            ]
            if links:
                promo_repo.link_tasks(conn, run["id"], links)
            run = _transition(
                conn, run, "executing", event_type="execute_started", source="promote-develop"
            )

        # Git 写在事务外
        bare = root / BARE_DIR_NAME
        write_ok = False
        try:
            adapter.push_fast_forward(
                bare,
                plan["remote"],
                plan["source_ref"],
                plan["target_ref"],
                old_sha,
                source_sha,
            )
            write_ok = True
            observed = adapter._live_remote_tip(bare, plan["remote"], "develop")
            if observed != source_sha:
                with immediate_transaction(conn):
                    run = promo_repo.get_run(conn, run["id"])  # type: ignore[assignment]
                    assert run is not None
                    run = _transition(
                        conn,
                        run,
                        "reconciling",
                        event_type="postcheck_mismatch",
                        source="promote-develop",
                        detail={"observed": observed, "expected": source_sha},
                        observed_target_sha=observed,
                        last_error="post-check SHA mismatch",
                    )
                    # 简化：若 observed == old → failed_safe；第三 SHA → manual
                    if observed == old_sha:
                        run = _transition(
                            conn,
                            run,
                            "failed_safe_to_retry",
                            event_type="reconcile_old_sha",
                            source="promote-develop",
                        )
                    else:
                        run = _transition(
                            conn,
                            run,
                            "manual_required",
                            event_type="reconcile_third_sha",
                            source="promote-develop",
                        )
                return {
                    "project": project,
                    "dry_run": False,
                    "execute": True,
                    "promotion": run,
                    "write_performed": write_ok,
                    "error_kind": "promotion_postcheck_failed",
                }

            with immediate_transaction(conn):
                run = promo_repo.get_run(conn, run["id"])  # type: ignore[assignment]
                assert run is not None
                run = _transition(
                    conn,
                    run,
                    "succeeded",
                    event_type="published",
                    source="promote-develop",
                    detail={"published_sha": source_sha},
                    published_sha=source_sha,
                    observed_target_sha=observed,
                )
            return {
                "project": project,
                "dry_run": False,
                "execute": True,
                "promotion": run,
                "plan": plan,
                "write_performed": True,
            }
        except OrchError as exc:
            kind = getattr(exc, "kind", None) or "promotion_push_failed"
            with immediate_transaction(conn):
                run = promo_repo.get_run(conn, run["id"])  # type: ignore[assignment]
                assert run is not None
                if kind == "remote_cas_race":
                    tip = adapter._live_remote_tip(bare, plan["remote"], "develop")
                    run = _transition(
                        conn,
                        run,
                        "reconciling",
                        event_type="push_cas_race",
                        source="promote-develop",
                        detail={"error": str(exc), "tip": tip},
                        last_error=str(exc)[:400],
                        observed_target_sha=tip,
                    )
                    if tip == source_sha:
                        run = _transition(
                            conn,
                            run,
                            "succeeded",
                            event_type="reconcile_already_published",
                            source="promote-develop",
                            published_sha=source_sha,
                            observed_target_sha=tip,
                        )
                    elif tip == old_sha:
                        run = _transition(
                            conn,
                            run,
                            "failed_safe_to_retry",
                            event_type="reconcile_old_sha",
                            source="promote-develop",
                        )
                    else:
                        run = _transition(
                            conn,
                            run,
                            "manual_required",
                            event_type="reconcile_third_sha",
                            source="promote-develop",
                        )
                else:
                    run = _transition(
                        conn,
                        run,
                        "failed_safe_to_retry",
                        event_type="push_failed",
                        source="promote-develop",
                        detail={"error": str(exc), "kind": kind},
                        last_error=str(exc)[:400],
                    )
            return {
                "project": project,
                "dry_run": False,
                "execute": True,
                "promotion": run,
                "write_performed": write_ok,
                "error_kind": kind,
                "error": str(exc),
            }
    except PrecheckBlocked as exc:
        return {
            "project": project,
            "dry_run": False,
            "execute": True,
            "ok_to_execute": False,
            "write_performed": False,
            "error_kind": exc.kind,
            "error": str(exc),
            "details": getattr(exc, "details", None),
        }
    finally:
        conn.close()
        release(handle)
