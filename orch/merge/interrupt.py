"""SIGINT / KeyboardInterrupt reconcilation after merge Do (§11.1)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from orch.constants import MAIN_WORKTREE_NAME, TARGET_BRANCH
from orch.git.ref import run_git_ref
from orch.git.worktree import run_git_worktree
from orch.merge.do import capture_conflict_files
from orch.merge.finalize import finalize_recovery, finalize_success, post_check_success
from orch.merge.recover import _to_conflict, _to_pending
from orch.state_machine import assert_transition


def _merge_head_path(main: Path) -> Path | None:
    r = run_git_worktree(["rev-parse", "--git-path", "MERGE_HEAD"], main)
    if not r.ok:
        return None
    mh = Path(r.stdout.strip())
    if not mh.is_absolute():
        mh = main / mh
    return mh


def _main_state(main: Path) -> dict[str, Any]:
    head_br = run_git_worktree(["rev-parse", "--abbrev-ref", "HEAD"], main)
    head = run_git_worktree(["rev-parse", "HEAD"], main)
    st = run_git_worktree(["status", "--porcelain"], main)
    mh = _merge_head_path(main)
    return {
        "branch": head_br.stdout.strip() if head_br.ok else None,
        "head": head.stdout.strip() if head.ok else None,
        "porcelain": st.stdout if st.ok else None,
        "merge_head": bool(mh and mh.exists()),
        "clean": bool(
            head_br.ok
            and head_br.stdout.strip() == TARGET_BRANCH
            and st.ok
            and not st.stdout.strip()
            and not (mh and mh.exists())
        ),
    }


def reconcile_after_interrupt(
    conn: sqlite3.Connection,
    root: Path,
    bare: Path,
    task: dict[str, Any],
) -> dict[str, Any]:
    """
    After Git child is stopped: abort if needed, then evidence-based status.

    Returns detail dict including recovered_as: pending | merged | conflict | recovery_required.
    """
    main = root / MAIN_WORKTREE_NAME
    source = task["source_commit"]
    target_claim = task.get("target_commit_at_claim")
    state = _main_state(main)

    if state["merge_head"]:
        files = capture_conflict_files(main)
        abort = run_git_worktree(["merge", "--abort"], main)
        state2 = _main_state(main)
        if not abort.ok or not state2["clean"]:
            return finalize_recovery(
                conn,
                task,
                f"interrupt: merge --abort failed or main dirty ({abort.stderr.strip()})",
                conflict_files=files,
            )
        # aborted clean
        if target_claim and state2["head"] == target_claim:
            # may still be conflict-class interrupt; prefer pending if no evidence of finish
            return _to_pending(conn, task, "interrupt_aborted_pending")
        # if develop already has source after abort (unlikely mid-merge), check
        anc = run_git_ref(["merge-base", "--is-ancestor", source, TARGET_BRANCH], bare)
        if anc.ok and state2["clean"]:
            ok, _ = post_check_success(root, bare, source)
            if ok:
                # treat as merged path via finalize_success fields
                return finalize_success(conn, root, bare, task)
        if files:
            # was in conflict when interrupted
            try:
                return _to_conflict(conn, task, files, "interrupt_conflict")
            except Exception:
                return finalize_recovery(
                    conn, task, "interrupt: could not mark conflict", conflict_files=files
                )
        return _to_pending(conn, task, "interrupt_aborted_pending")

    # No MERGE_HEAD — either not started, finished, or rolled back
    anc = run_git_ref(["merge-base", "--is-ancestor", source, TARGET_BRANCH], bare)
    if anc.ok and state["clean"]:
        ok, reason = post_check_success(root, bare, source)
        if ok:
            return finalize_success(conn, root, bare, task)
        return finalize_recovery(conn, task, f"interrupt post-check failed: {reason}")

    if (
        state["clean"]
        and target_claim
        and state["head"] == target_claim
    ):
        return _to_pending(conn, task, "interrupt_not_started")

    return finalize_recovery(
        conn,
        task,
        "interrupt: unreconciled main state",
        conflict_files=None,
    )
