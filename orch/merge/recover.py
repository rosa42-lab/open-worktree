"""Evidence-based reset-stuck recovery (task T-0503)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from orch.audit import write_audit
from orch.constants import MAIN_WORKTREE_NAME, TARGET_BRANCH
from orch.db import immediate_transaction
from orch.git.ref import run_git_ref
from orch.git.worktree import run_git_worktree
from orch.merge.do import capture_conflict_files
from orch.state_machine import assert_transition
from orch.util import utc_now_iso


def _main_clean_on_develop(main: Path) -> tuple[bool, dict[str, Any]]:
    info: dict[str, Any] = {}
    head = run_git_worktree(["rev-parse", "--abbrev-ref", "HEAD"], main)
    info["branch"] = head.stdout.strip() if head.ok else None
    st = run_git_worktree(["status", "--porcelain"], main)
    info["porcelain"] = st.stdout if st.ok else None
    mh_r = run_git_worktree(["rev-parse", "--git-path", "MERGE_HEAD"], main)
    mh = Path(mh_r.stdout.strip()) if mh_r.ok else None
    if mh is not None and not mh.is_absolute():
        mh = main / mh
    info["merge_head"] = bool(mh and mh.exists())
    ok = (
        head.ok
        and head.stdout.strip() == TARGET_BRANCH
        and st.ok
        and not st.stdout.strip()
        and not info["merge_head"]
    )
    return ok, info


def recover_task(
    conn: sqlite3.Connection,
    root: Path,
    bare: Path,
    task: dict[str, Any],
) -> dict[str, Any]:
    main = root / MAIN_WORKTREE_NAME
    source = task["source_commit"]
    target_claim = task["target_commit_at_claim"]
    develop = run_git_ref(["rev-parse", TARGET_BRANCH], bare, check=True).stdout.strip()
    clean, minfo = _main_clean_on_develop(main)

    # 1) develop contains source and main clean -> merged
    anc = run_git_ref(["merge-base", "--is-ancestor", source, TARGET_BRANCH], bare)
    if anc.ok and clean:
        head = run_git_worktree(["rev-parse", "HEAD"], main, check=True).stdout.strip()
        finished = utc_now_iso()
        with immediate_transaction(conn) as c:
            if task["status"] == "merging":
                assert_transition("merging", "merged")
            elif task["status"] == "recovery_required":
                assert_transition("recovery_required", "merged")
            c.execute(
                """
                UPDATE tasks SET
                  status = 'merged',
                  merged_commit = ?,
                  finished_at = ?,
                  last_error = NULL
                WHERE id = ?
                """,
                (head, finished, task["id"]),
            )
            write_audit(
                c,
                "reset_stuck",
                task_id=task["id"],
                detail={"recovered_as": "merged", "merged_commit": head},
            )
        return {"task_id": task["id"], "recovered_as": "merged", "merged_commit": head}

    # 3) MERGE_HEAD present — try abort once
    if minfo.get("merge_head"):
        files = capture_conflict_files(main)
        abort = run_git_worktree(["merge", "--abort"], main)
        clean2, minfo2 = _main_clean_on_develop(main)
        head2 = run_git_worktree(["rev-parse", "HEAD"], main)
        head_sha = head2.stdout.strip() if head2.ok else None
        if abort.ok and clean2:
            if target_claim and head_sha == target_claim:
                return _to_pending(conn, task, "pending_after_abort")
            # conflict residual evidence
            return _to_conflict(conn, task, files, "conflict_after_abort")
        return _to_recovery(conn, task, "abort_failed_or_dirty", minfo2)

    # 2) HEAD == target_commit_at_claim and clean -> pending
    head = run_git_worktree(["rev-parse", "HEAD"], main)
    head_sha = head.stdout.strip() if head.ok else None
    if (
        clean
        and target_claim
        and head_sha == target_claim
    ):
        return _to_pending(conn, task, "pending")

    # 4) manual
    return _to_recovery(
        conn,
        task,
        "manual_required",
        {
            "develop": develop,
            "head": head_sha,
            "target_commit_at_claim": target_claim,
            "main": minfo,
        },
    )


def _to_pending(conn, task, recovered_as: str) -> dict[str, Any]:
    with immediate_transaction(conn) as c:
        if task["status"] == "merging":
            assert_transition("merging", "pending")
        elif task["status"] == "recovery_required":
            assert_transition("recovery_required", "pending")
        c.execute(
            """
            UPDATE tasks SET
              status = 'pending',
              claimed_at = NULL,
              target_commit_at_claim = NULL,
              finished_at = NULL,
              last_error = NULL,
              conflict_files = NULL
            WHERE id = ?
            """,
            (task["id"],),
        )
        write_audit(
            c,
            "reset_stuck",
            task_id=task["id"],
            detail={"recovered_as": recovered_as},
        )
    return {"task_id": task["id"], "recovered_as": "pending"}


def _to_conflict(conn, task, files: list[str], recovered_as: str) -> dict[str, Any]:
    finished = utc_now_iso()
    with immediate_transaction(conn) as c:
        assert_transition("merging", "conflict")
        c.execute(
            """
            UPDATE tasks SET
              status = 'conflict',
              finished_at = ?,
              conflict_files = ?,
              last_error = ?
            WHERE id = ?
            """,
            (finished, json.dumps(files), recovered_as, task["id"]),
        )
        write_audit(
            c,
            "reset_stuck",
            task_id=task["id"],
            detail={"recovered_as": "conflict", "conflict_files": files},
        )
    return {"task_id": task["id"], "recovered_as": "conflict", "conflict_files": files}


def _to_recovery(conn, task, reason: str, evidence: dict[str, Any]) -> dict[str, Any]:
    finished = utc_now_iso()
    with immediate_transaction(conn) as c:
        if task["status"] == "merging":
            assert_transition("merging", "recovery_required")
        c.execute(
            """
            UPDATE tasks SET
              status = 'recovery_required',
              finished_at = ?,
              last_error = ?
            WHERE id = ?
            """,
            (finished, reason[:2000], task["id"]),
        )
        write_audit(
            c,
            "reset_stuck",
            task_id=task["id"],
            detail={"recovered_as": "manual_required", "reason": reason, "evidence": evidence},
        )
    return {
        "task_id": task["id"],
        "recovered_as": "manual_required",
        "reason": reason,
        "evidence": evidence,
    }
