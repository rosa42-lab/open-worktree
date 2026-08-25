"""orch <project> enqueue."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Any

from orch.audit import write_audit
from orch.constants import BARE_DIR_NAME, TARGET_BRANCH, project_lock_path
from orch.db import immediate_transaction, open_project_db
from orch.errors import ValidationError
from orch.git.parser import check_ref_format_branch
from orch.git.ref import run_git_ref
from orch.git.worktree import assert_worktree_owns_bare, run_git_worktree
from orch.locks import acquire, release
from orch.registry import get_project_path
from orch.state_machine import assert_transition
from orch.util import utc_now_iso
from orch.validate import normalize_path, validate_agent_name, validate_project_name


def _git_enqueue_precheck(
    *,
    bare: Path,
    wt: Path,
    branch: str,
) -> tuple[str, str, int]:
    assert_worktree_owns_bare(wt, bare)
    head_br = run_git_worktree(["rev-parse", "--abbrev-ref", "HEAD"], wt, check=True)
    if head_br.stdout.strip() != branch:
        raise ValidationError(
            f"worktree HEAD branch is {head_br.stdout.strip()!r}, expected {branch!r}",
            kind="enqueue_validation_failed",
            details={"head": head_br.stdout.strip(), "branch": branch},
        )
    porcelain = run_git_worktree(["status", "--porcelain"], wt, check=True)
    if porcelain.stdout.strip():
        raise ValidationError(
            "worktree is not clean",
            kind="enqueue_validation_failed",
            details={"porcelain": porcelain.stdout},
        )
    br_ok = run_git_ref(["rev-parse", "--verify", branch], bare)
    if not br_ok.ok:
        raise ValidationError(
            f"branch not found in bare: {branch}",
            kind="enqueue_validation_failed",
            details={"stderr": br_ok.stderr},
        )
    source = run_git_ref(["rev-parse", branch], bare, check=True).stdout.strip()
    target = run_git_ref(["rev-parse", TARGET_BRANCH], bare, check=True).stdout.strip()
    count_r = run_git_ref(
        ["rev-list", "--count", f"{TARGET_BRANCH}..{source}"],
        bare,
        check=True,
    )
    try:
        n = int(count_r.stdout.strip())
    except ValueError:
        n = 0
    if n <= 0:
        raise ValidationError(
            "no commits to merge (empty change)",
            kind="enqueue_validation_failed",
            details={"source_commit": source, "target": target, "count": n},
        )
    return source, target, n


def enqueue_unlocked(
    conn: sqlite3.Connection,
    project: str,
    agent: str,
    branch: str,
    worktree_path: str,
    *,
    priority: int = 1,
    topic_id: str | None = None,
    expected_source: str | None = None,
) -> dict[str, Any]:
    """Git precheck + INSERT. Caller must already hold project.lock. No nested acquire."""
    root = get_project_path(project)
    bare = (root / BARE_DIR_NAME).resolve()
    wt = normalize_path(worktree_path, label="worktree_path")
    if not wt.exists():
        raise ValidationError(
            f"worktree path does not exist: {wt}",
            kind="enqueue_validation_failed",
            details={"worktree_path": str(wt)},
        )
    source, target, _n = _git_enqueue_precheck(bare=bare, wt=wt, branch=branch)
    if expected_source is not None and source != expected_source:
        raise ValidationError(
            "enqueue source_commit does not match verification SHA",
            kind="topic_verification_sha_mismatch",
            details={
                "source_commit": source,
                "verification_sha": expected_source,
            },
        )

    if topic_id is None:
        live = conn.execute(
            """
            SELECT id, name FROM topics
            WHERE project_name = ?
              AND lifecycle_state NOT IN ('archived', 'cancelled', 'merged', 'rejected')
              AND (branch_name = ? OR worktree_path = ?)
            """,
            (project, branch, str(wt)),
        ).fetchone()
        if live is not None:
            raise ValidationError(
                "worktree/branch belongs to a live topic; use topic-enqueue",
                kind="topic_enqueue_required",
                details={"topic_id": live["id"], "name": live["name"]},
            )

    task_id = uuid.uuid4().hex
    submitted = utc_now_iso()
    assert_transition(None, "pending")
    try:
        with immediate_transaction(conn) as c:
            c.execute(
                "UPDATE counters SET value = value + 1 WHERE name = 'queue_seq'"
            )
            row = c.execute(
                "SELECT value FROM counters WHERE name = 'queue_seq'"
            ).fetchone()
            queue_seq = int(row["value"])
            c.execute(
                """
                INSERT INTO tasks(
                  id, agent_name, branch_name, worktree_path, priority, status,
                  submitted_at, source_commit, target_head_before,
                  target_commit_at_claim, queue_seq, claimed_at, finished_at,
                  merged_commit, last_error, conflict_files, attempts, archived_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    task_id,
                    agent,
                    branch,
                    str(wt),
                    priority,
                    "pending",
                    submitted,
                    source,
                    target,
                    None,
                    queue_seq,
                    None,
                    None,
                    None,
                    None,
                    None,
                    0,
                    None,
                ),
            )
            if topic_id:
                c.execute(
                    """
                    UPDATE topics
                    SET task_id = ?, lifecycle_state = 'enqueued',
                        last_step = 'enqueue', last_error = NULL, updated_at = ?
                    WHERE id = ? AND project_name = ?
                    """,
                    (task_id, submitted, topic_id, project),
                )
                if c.execute("SELECT changes()").fetchone()[0] == 0:
                    raise ValidationError(
                        f"topic not found for enqueue: {topic_id}",
                        kind="topic_not_found",
                        details={"topic_id": topic_id},
                    )
                c.execute(
                    """
                    UPDATE agent_runs
                    SET task_id = ?, topic_id = ?, updated_at = ?
                    WHERE topic_id = ? AND project_name = ?
                      AND state NOT IN ('exited', 'archived')
                    """,
                    (task_id, topic_id, submitted, topic_id, project),
                )
            write_audit(
                c,
                "enqueued",
                task_id=task_id,
                detail={
                    "agent": agent,
                    "branch": branch,
                    "source_commit": source,
                    "target_head_before": target,
                    "priority": priority,
                    "queue_seq": queue_seq,
                    "topic_id": topic_id,
                },
            )
    except sqlite3.IntegrityError as exc:
        raise ValidationError(
            f"active task already exists for branch {branch}",
            kind="enqueue_validation_failed",
            details={"branch": branch, "error": str(exc)},
        ) from exc

    return {
        "task_id": task_id,
        "agent": agent,
        "branch": branch,
        "worktree_path": str(wt),
        "priority": priority,
        "status": "pending",
        "source_commit": source,
        "target_head_before": target,
        "queue_seq": queue_seq,
        "submitted_at": submitted,
        "topic_id": topic_id,
    }


def cmd_enqueue(
    project: str,
    agent: str,
    branch: str,
    worktree_path: str,
    *,
    priority: int = 1,
) -> dict[str, Any]:
    project = validate_project_name(project)
    agent = validate_agent_name(agent)
    check_ref_format_branch(branch)
    if priority < 0:
        raise ValidationError(
            "priority must be >= 0",
            kind="enqueue_validation_failed",
            details={"priority": priority},
        )

    conn = open_project_db(project, init=True)
    handle = None
    try:
        handle = acquire(
            project_lock_path(project),
            command="enqueue",
            project=project,
            audit_conn=conn,
        )
        return enqueue_unlocked(
            conn, project, agent, branch, worktree_path, priority=priority
        )
    finally:
        if handle is not None:
            release(handle, audit_conn=conn)
        conn.close()
