"""orch <project> merge [--once]."""

from __future__ import annotations

from typing import Any

from orch.constants import BARE_DIR_NAME, project_lock_path
from orch.db import open_project_db
from orch.errors import (
    ExitCode,
    InterruptedMergeError,
    OrchError,
    PrecheckError,
    QueueBlockedError,
)
from orch.locks import acquire, release
from orch.merge.claim import audit_precheck_failure, claim_next, precheck_main
from orch.merge.do import audit_merge_started, run_merge_no_ff
from orch.merge.finalize import finalize_from_result
from orch.registry import get_project_path
from orch.validate import validate_project_name


def cmd_merge(project: str, *, once: bool = False) -> dict[str, Any]:
    project = validate_project_name(project)
    root = get_project_path(project)
    bare = (root / BARE_DIR_NAME).resolve()
    conn = open_project_db(project, init=True)
    handle = None
    results: list[dict[str, Any]] = []
    try:
        handle = acquire(
            project_lock_path(project),
            command="merge",
            project=project,
            audit_conn=conn,
        )
        while True:
            try:
                target_for_claim = precheck_main(root, bare)
            except PrecheckError as exc:
                audit_precheck_failure(conn, exc.message)
                raise

            try:
                task = claim_next(conn, target_for_claim, project_name=project)
            except QueueBlockedError:
                raise

            if task is None:
                if not results:
                    return {"processed": [], "message": "no pending tasks"}
                break

            audit_merge_started(conn, task["id"])
            try:
                result = run_merge_no_ff(root, task["source_commit"])
            except KeyboardInterrupt as exc:
                from orch.merge.interrupt import reconcile_after_interrupt

                recovered = reconcile_after_interrupt(conn, root, bare, task)
                raise InterruptedMergeError(
                    "merge interrupted",
                    details={
                        "task_id": task["id"],
                        "recovered": recovered,
                    },
                ) from exc

            outcome = finalize_from_result(conn, root, bare, task, result)
            results.append(outcome)
            if outcome.get("status") in ("conflict", "recovery_required"):
                break
            if once:
                break
        data: dict[str, Any] = {"processed": results}
        if results and results[-1].get("status") == "recovery_required":
            raise OrchError(
                results[-1].get("reason") or "recovery required",
                code=ExitCode.GIT,
                kind="merge_aborted_recovery_required",
                details=results[-1],
            )
        return data
    finally:
        if handle is not None:
            release(handle, audit_conn=conn)
        conn.close()
