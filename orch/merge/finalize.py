"""Merge finalize success / conflict / recovery paths."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from orch.audit import write_audit
from orch.constants import BARE_DIR_NAME, MAIN_WORKTREE_NAME, TARGET_BRANCH
from orch.db import immediate_transaction
from orch.git.ref import run_git_ref
from orch.git.worktree import run_git_worktree
from orch.merge.do import capture_conflict_files, is_merge_conflict
from orch.git._runner import GitResult
from orch.state_machine import assert_transition
from orch.util import utc_now_iso


def post_check_success(root: Path, bare: Path, source_commit: str) -> tuple[bool, str]:
    main = root / MAIN_WORKTREE_NAME
    st = run_git_worktree(["status", "--porcelain"], main)
    if st.stdout.strip():
        return False, "main not clean after merge"
    mh_path = run_git_worktree(["rev-parse", "--git-path", "MERGE_HEAD"], main, check=True)
    mh = Path(mh_path.stdout.strip())
    if not mh.is_absolute():
        mh = main / mh
    if mh.exists():
        return False, "MERGE_HEAD still present"
    anc = run_git_ref(
        ["merge-base", "--is-ancestor", source_commit, TARGET_BRANCH],
        bare,
    )
    if not anc.ok:
        return False, "source_commit is not ancestor of develop"
    return True, ""


def finalize_success(
    conn: sqlite3.Connection,
    root: Path,
    bare: Path,
    task: dict[str, Any],
) -> dict[str, Any]:
    main = root / MAIN_WORKTREE_NAME
    head = run_git_worktree(["rev-parse", "HEAD"], main, check=True).stdout.strip()
    ok, reason = post_check_success(root, bare, task["source_commit"])
    if not ok:
        return finalize_recovery(conn, task, reason)
    finished = utc_now_iso()
    with immediate_transaction(conn) as c:
        assert_transition("merging", "merged")
        c.execute(
            """
            UPDATE tasks SET
              status = 'merged',
              merged_commit = ?,
              finished_at = ?,
              last_error = NULL,
              conflict_files = NULL
            WHERE id = ?
            """,
            (head, finished, task["id"]),
        )
        write_audit(
            c,
            "merge_succeeded",
            task_id=task["id"],
            detail={"merged_commit": head},
        )
    return {"status": "merged", "merged_commit": head, "task_id": task["id"]}


def finalize_from_result(
    conn: sqlite3.Connection,
    root: Path,
    bare: Path,
    task: dict[str, Any],
    result: GitResult,
) -> dict[str, Any]:
    main = root / MAIN_WORKTREE_NAME
    if result.returncode == 0:
        return finalize_success(conn, root, bare, task)
    if is_merge_conflict(result, main):
        return finalize_conflict(conn, root, task, result)
    return finalize_recovery(
        conn,
        task,
        result.stderr.strip() or result.stdout.strip() or "git merge failed",
        conflict_files=None,
    )


def finalize_conflict(
    conn: sqlite3.Connection,
    root: Path,
    task: dict[str, Any],
    result: GitResult,
) -> dict[str, Any]:
    main = root / MAIN_WORKTREE_NAME
    files = capture_conflict_files(main)
    abort = run_git_worktree(["merge", "--abort"], main)
    if not abort.ok:
        return finalize_recovery(
            conn,
            task,
            f"merge --abort failed: {abort.stderr}",
            conflict_files=files,
        )
    finished = utc_now_iso()
    with immediate_transaction(conn) as c:
        assert_transition("merging", "conflict")
        c.execute(
            """
            UPDATE tasks SET
              status = 'conflict',
              finished_at = ?,
              last_error = ?,
              conflict_files = ?
            WHERE id = ?
            """,
            (
                finished,
                (result.stderr or result.stdout)[:2000],
                json.dumps(files),
                task["id"],
            ),
        )
        write_audit(
            c,
            "merge_aborted_conflict",
            task_id=task["id"],
            detail={"conflict_files": files},
        )
    return {
        "status": "conflict",
        "task_id": task["id"],
        "conflict_files": files,
    }


def finalize_recovery(
    conn: sqlite3.Connection,
    task: dict[str, Any],
    reason: str,
    conflict_files: list[str] | None = None,
) -> dict[str, Any]:
    finished = utc_now_iso()
    with immediate_transaction(conn) as c:
        # allow from merging only; if already wrong, still try
        cur = c.execute("SELECT status FROM tasks WHERE id = ?", (task["id"],)).fetchone()
        if cur and cur["status"] == "merging":
            assert_transition("merging", "recovery_required")
        c.execute(
            """
            UPDATE tasks SET
              status = 'recovery_required',
              finished_at = ?,
              last_error = ?,
              conflict_files = COALESCE(?, conflict_files)
            WHERE id = ?
            """,
            (
                finished,
                reason[:2000],
                json.dumps(conflict_files) if conflict_files is not None else None,
                task["id"],
            ),
        )
        write_audit(
            c,
            "merge_aborted_recovery_required",
            task_id=task["id"],
            detail={"reason": reason, "conflict_files": conflict_files},
        )
    return {
        "status": "recovery_required",
        "task_id": task["id"],
        "reason": reason,
        "exit_hint": 8,
    }
