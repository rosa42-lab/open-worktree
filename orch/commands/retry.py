"""orch <project> retry — DB only after Git validation (no Git writes)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from orch.audit import write_audit
from orch.constants import BARE_DIR_NAME, TARGET_BRANCH, project_lock_path
from orch.db import immediate_transaction, open_project_db
from orch.errors import ValidationError
from orch.git.ref import run_git_ref
from orch.git.worktree import assert_worktree_owns_bare, run_git_worktree
from orch.locks import acquire, release
from orch.registry import get_project_path
from orch.state_machine import assert_transition
from orch.task_resolve import resolve_task
from orch.validate import validate_project_name


def cmd_retry(project: str, task_id: str) -> dict[str, Any]:
    project = validate_project_name(project)
    root = get_project_path(project)
    bare = (root / BARE_DIR_NAME).resolve()
    conn = open_project_db(project, init=True)
    handle = None
    try:
        handle = acquire(
            project_lock_path(project),
            command="retry",
            project=project,
            audit_conn=conn,
        )
        task = resolve_task(conn, task_id)
        if task["status"] != "conflict":
            raise ValidationError(
                f"retry only allowed for conflict tasks; got {task['status']}",
                kind="retry_validation_failed",
                details={"status": task["status"], "task_id": task["id"]},
            )

        wt = Path(task["worktree_path"])
        branch = task["branch_name"]
        old_source = task["source_commit"]

        # All Git reads / validation — no Git writes
        assert_worktree_owns_bare(wt, bare)
        porcelain = run_git_worktree(["status", "--porcelain"], wt, check=True)
        if porcelain.stdout.strip():
            raise ValidationError(
                "worktree is not clean",
                kind="retry_validation_failed",
                details={"porcelain": porcelain.stdout},
            )
        head_br = run_git_worktree(["rev-parse", "--abbrev-ref", "HEAD"], wt, check=True)
        if head_br.stdout.strip() != branch:
            raise ValidationError(
                "worktree branch mismatch",
                kind="retry_validation_failed",
                details={"head": head_br.stdout.strip(), "branch": branch},
            )
        wt_head = run_git_worktree(["rev-parse", "HEAD"], wt, check=True).stdout.strip()
        bare_head = run_git_ref(["rev-parse", branch], bare, check=True).stdout.strip()
        if wt_head != bare_head:
            raise ValidationError(
                "worktree HEAD != bare branch HEAD",
                kind="retry_validation_failed",
                details={"worktree_head": wt_head, "bare_head": bare_head},
            )
        if wt_head == old_source:
            raise ValidationError(
                "source_commit unchanged; create a new commit after merging develop",
                kind="retry_validation_failed",
                details={"source_commit": old_source},
            )
        anc = run_git_ref(
            ["merge-base", "--is-ancestor", TARGET_BRANCH, wt_head],
            bare,
        )
        if not anc.ok:
            raise ValidationError(
                "new source does not contain current develop (merge develop first)",
                kind="retry_validation_failed",
                details={"new_source": wt_head},
            )

        with immediate_transaction(conn) as c:
            row = c.execute("SELECT status FROM tasks WHERE id = ?", (task["id"],)).fetchone()
            if not row or row["status"] != "conflict":
                raise ValidationError(
                    "task is no longer conflict",
                    kind="retry_validation_failed",
                    details={"task_id": task["id"]},
                )
            assert_transition("conflict", "pending")
            c.execute(
                """
                UPDATE tasks SET
                  status = 'pending',
                  source_commit = ?,
                  target_commit_at_claim = NULL,
                  claimed_at = NULL,
                  finished_at = NULL,
                  last_error = NULL,
                  conflict_files = NULL,
                  attempts = 0
                WHERE id = ?
                """,
                (wt_head, task["id"]),
            )
            write_audit(
                c,
                "retried",
                task_id=task["id"],
                detail={
                    "old_source_commit": old_source,
                    "new_source_commit": wt_head,
                },
            )
        return {
            "task_id": task["id"],
            "status": "pending",
            "old_source_commit": old_source,
            "new_source_commit": wt_head,
        }
    finally:
        if handle is not None:
            release(handle, audit_conn=conn)
        conn.close()
