"""orch <project> skip."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from orch.audit import write_audit
from orch.constants import MAIN_WORKTREE_NAME, TARGET_BRANCH, project_lock_path
from orch.db import immediate_transaction, open_project_db
from orch.errors import ValidationError
from orch.git.worktree import run_git_worktree
from orch.locks import acquire, release
from orch.registry import get_project_path
from orch.state_machine import assert_transition
from orch.task_resolve import resolve_task
from orch.util import utc_now_iso
from orch.validate import validate_project_name


def cmd_skip(project: str, task_id: str, *, reason: str = "") -> dict[str, Any]:
    project = validate_project_name(project)
    root = get_project_path(project)
    conn = open_project_db(project, init=True)
    handle = None
    try:
        handle = acquire(
            project_lock_path(project),
            command="skip",
            project=project,
            audit_conn=conn,
        )
        task = resolve_task(conn, task_id)
        status = task["status"]
        if status not in ("pending", "conflict"):
            raise ValidationError(
                f"cannot skip task in status {status}",
                kind="skip_validation_failed",
                details={"status": status},
            )
        if status == "conflict":
            main = root / MAIN_WORKTREE_NAME
            head = run_git_worktree(["rev-parse", "--abbrev-ref", "HEAD"], main, check=True)
            if head.stdout.strip() != TARGET_BRANCH:
                raise ValidationError(
                    "main/ not on develop; fix with reset-stuck first",
                    kind="skip_validation_failed",
                )
            st = run_git_worktree(["status", "--porcelain"], main, check=True)
            if st.stdout.strip():
                raise ValidationError(
                    "main/ not clean; fix with reset-stuck first",
                    kind="skip_validation_failed",
                    details={"porcelain": st.stdout},
                )
            mh_r = run_git_worktree(["rev-parse", "--git-path", "MERGE_HEAD"], main, check=True)
            mh = Path(mh_r.stdout.strip())
            if not mh.is_absolute():
                mh = main / mh
            if mh.exists():
                raise ValidationError(
                    "MERGE_HEAD present; fix with reset-stuck first",
                    kind="skip_validation_failed",
                )

        finished = utc_now_iso()
        with immediate_transaction(conn) as c:
            assert_transition(status, "skipped")
            c.execute(
                """
                UPDATE tasks SET status = 'skipped', finished_at = ?, last_error = ?
                WHERE id = ?
                """,
                (finished, reason or None, task["id"]),
            )
            write_audit(
                c,
                "skipped",
                task_id=task["id"],
                detail={"reason": reason},
            )
        return {"task_id": task["id"], "status": "skipped", "reason": reason}
    finally:
        if handle is not None:
            release(handle, audit_conn=conn)
        conn.close()
