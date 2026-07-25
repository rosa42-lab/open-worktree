"""Merge precheck and claim stages."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from orch.audit import write_audit
from orch.constants import MAIN_WORKTREE_NAME, TARGET_BRANCH
from orch.db import immediate_transaction
from orch.errors import PrecheckError, QueueBlockedError
from orch.git.worktree import resolve_common_dir, run_git_worktree
from orch.state_machine import assert_transition
from orch.util import utc_now_iso


def precheck_main(root: Path, bare: Path) -> str:
    """Return develop HEAD at main for claim. Raises PrecheckError."""
    main = root / MAIN_WORKTREE_NAME
    if not main.is_dir():
        raise PrecheckError("main/ missing", details={"main": str(main)})
    common = resolve_common_dir(main)
    if common != bare.resolve():
        raise PrecheckError(
            "main/ does not belong to project bare",
            details={"common": str(common), "bare": str(bare.resolve())},
        )
    head = run_git_worktree(["rev-parse", "--abbrev-ref", "HEAD"], main, check=True)
    if head.stdout.strip() != TARGET_BRANCH:
        raise PrecheckError(
            f"main/ not on {TARGET_BRANCH}",
            details={"head": head.stdout.strip()},
        )
    st = run_git_worktree(["status", "--porcelain"], main, check=True)
    if st.stdout.strip():
        raise PrecheckError("main/ is not clean", details={"porcelain": st.stdout})
    merge_head_path = run_git_worktree(
        ["rev-parse", "--git-path", "MERGE_HEAD"], main, check=True
    ).stdout.strip()
    mh = Path(merge_head_path)
    if not mh.is_absolute():
        mh = main / mh
    if mh.exists():
        raise PrecheckError("main/ has MERGE_HEAD (merge in progress)")
    target = run_git_worktree(["rev-parse", TARGET_BRANCH], main, check=True)
    return target.stdout.strip()


def claim_next(
    conn: sqlite3.Connection,
    target_commit_for_claim: str,
) -> dict[str, Any] | None:
    """Claim next pending task. Returns task dict or None if empty."""
    with immediate_transaction(conn) as c:
        blocked = c.execute(
            """
            SELECT id, status FROM tasks
            WHERE status IN ('conflict', 'recovery_required')
            LIMIT 1
            """
        ).fetchone()
        if blocked is not None:
            write_audit(
                c,
                "merge_aborted_precheck",
                task_id=blocked["id"],
                detail={
                    "reason": "queue_blocked",
                    "blocking_status": blocked["status"],
                },
            )
            raise QueueBlockedError(
                f"queue blocked by task {blocked['id']} status={blocked['status']}; "
                "use retry or reset-stuck",
                details={
                    "task_id": blocked["id"],
                    "status": blocked["status"],
                },
            )
        row = c.execute(
            """
            SELECT * FROM tasks
            WHERE status = 'pending'
            ORDER BY priority ASC, submitted_at ASC, queue_seq ASC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        assert_transition(row["status"], "merging")
        claimed_at = utc_now_iso()
        c.execute(
            """
            UPDATE tasks SET
              status = 'merging',
              claimed_at = ?,
              target_commit_at_claim = ?,
              attempts = attempts + 1
            WHERE id = ?
            """,
            (claimed_at, target_commit_for_claim, row["id"]),
        )
        write_audit(
            c,
            "merge_claimed",
            task_id=row["id"],
            detail={
                "target_commit_at_claim": target_commit_for_claim,
                "source_commit": row["source_commit"],
            },
        )
        updated = c.execute("SELECT * FROM tasks WHERE id = ?", (row["id"],)).fetchone()
        return {k: updated[k] for k in updated.keys()}


def audit_precheck_failure(conn: sqlite3.Connection, reason: str) -> None:
    with immediate_transaction(conn) as c:
        write_audit(
            c,
            "merge_aborted_precheck",
            detail={"reason": reason},
        )
