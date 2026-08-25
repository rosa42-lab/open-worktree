"""Topic + coordinator product workflow (V12-015 / V14) — core commands."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Any

from orch.agent_repo import attach_locator, get_run
from orch.constants import (
    BARE_DIR_NAME,
    MAIN_WORKTREE_NAME,
    TARGET_BRANCH,
    project_lock_path,
)
from orch.db import immediate_transaction, open_project_db
from orch.errors import ValidationError
from orch.git.parser import check_ref_format_branch
from orch.git.ref import run_git_ref
from orch.git.worktree import run_git_worktree, worktree_list_porcelain
from orch.locks import acquire, release
from orch.registry import get_project_path
from orch.runtime.registry import load_registry
from orch.runtime.takeover import agent_open, fork_inspect
from orch.util import utc_now_iso
from orch.validate import normalize_path


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _topic_dest(root: Path, agent: str | None, branch: str) -> Path:
    from orch.commands.worktree_add import worktree_dest

    return worktree_dest(root, agent or "topic", branch)


def _registered_worktrees(bare: Path) -> dict[Path, dict[str, str]]:
    out: dict[Path, dict[str, str]] = {}
    for entry in worktree_list_porcelain(bare):
        raw = entry.get("worktree")
        if not raw:
            continue
        out[Path(raw).resolve()] = entry
    return out


def _blocking_topic_runs(
    conn: sqlite3.Connection, topic_id: str
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, state FROM agent_runs
        WHERE topic_id = ?
          AND state NOT IN ('exited', 'archived')
        """,
        (topic_id,),
    ).fetchall()


def coordinator_bind(
    project: str,
    *,
    session_id: str,
    directory: str,
    runtime_server_id: str | None = None,
    replace: bool = False,
) -> dict[str, Any]:
    lock = acquire(
        project_lock_path(project), command="coordinator-bind", project=project
    )
    conn = open_project_db(project, init=True)
    try:
        reg = load_registry() or {}
        server_id = runtime_server_id or str(reg.get("server_id") or "unknown")
        active = conn.execute(
            """
            SELECT * FROM coordinator_sessions
            WHERE project_name = ? AND state IN ('active','unreachable')
            """,
            (project,),
        ).fetchone()
        now = utc_now_iso()
        if active is not None and not replace:
            raise ValidationError(
                "active coordinator already bound; pass --replace to rebind",
                kind="coordinator_active",
                details={"id": active["id"]},
            )
        generation = 1
        if active is not None and replace:
            generation = int(active["generation"]) + 1
            conn.execute(
                """
                UPDATE coordinator_sessions
                SET state = 'replaced', updated_at = ?, archived_at = ?
                WHERE id = ?
                """,
                (now, now, active["id"]),
            )
        cid = _new_id("coord")
        conn.execute(
            """
            INSERT INTO coordinator_sessions(
              id, project_name, runtime_server_id, session_id, directory,
              state, generation, created_at, updated_at
            ) VALUES (?,?,?,?,?, 'active', ?, ?, ?)
            """,
            (cid, project, server_id, session_id, directory, generation, now, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM coordinator_sessions WHERE id = ?", (cid,)
        ).fetchone()
        return {"coordinator": {k: row[k] for k in row.keys()}}
    finally:
        conn.close()
        release(lock)


def coordinator_show(project: str) -> dict[str, Any]:
    conn = open_project_db(project, init=True)
    try:
        row = conn.execute(
            """
            SELECT * FROM coordinator_sessions
            WHERE project_name = ? AND state IN ('active','unreachable')
            ORDER BY updated_at DESC LIMIT 1
            """,
            (project,),
        ).fetchone()
        if row is None:
            return {"coordinator": None}
        return {"coordinator": {k: row[k] for k in row.keys()}}
    finally:
        conn.close()


def topic_start(
    project: str,
    *,
    name: str,
    title: str,
    goal: str,
    branch_name: str,
    worktree_path: str | None = None,
    agent_name: str | None = None,
    brief: dict[str, Any] | None = None,
    provision_session: bool = False,
) -> dict[str, Any]:
    """Provision isolated branch+worktree. Does not enqueue. Session is optional."""
    check_ref_format_branch(branch_name)
    if branch_name == TARGET_BRANCH:
        raise ValidationError(
            "topic branch cannot be develop",
            kind="topic_isolation_required",
            details={"branch": branch_name},
        )

    root = get_project_path(project)
    bare = root / BARE_DIR_NAME
    dest = _topic_dest(root, agent_name, branch_name)
    main_wt = (root / MAIN_WORKTREE_NAME).resolve()
    if dest.resolve() == main_wt:
        raise ValidationError(
            "topic worktree cannot be main/",
            kind="topic_isolation_required",
            details={"path": str(dest)},
        )
    if worktree_path:
        given = normalize_path(worktree_path, label="worktree_path")
        if given != dest.resolve():
            raise ValidationError(
                "topic-start does not annotate an existing path; omit --worktree",
                kind="topic_annotate_forbidden",
                details={"computed": str(dest), "given": str(given)},
            )

    lock = acquire(project_lock_path(project), command="topic-start", project=project)
    conn = open_project_db(project, init=True)
    try:
        coord = conn.execute(
            """
            SELECT * FROM coordinator_sessions
            WHERE project_name = ? AND state = 'active'
            ORDER BY updated_at DESC LIMIT 1
            """,
            (project,),
        ).fetchone()
        if coord is None:
            raise ValidationError(
                "no active coordinator; run coordinator-bind first",
                kind="coordinator_missing",
            )

        existing = conn.execute(
            """
            SELECT * FROM topics
            WHERE project_name = ? AND name = ?
            """,
            (project, name),
        ).fetchone()

        registered = _registered_worktrees(bare)
        dest_key = dest.resolve()

        if existing is None and dest_key in registered:
            raise ValidationError(
                "destination is already a registered worktree; refusing to adopt",
                kind="topic_dest_occupied",
                details={"path": str(dest_key)},
            )
        if existing is None and dest.exists() and dest_key not in registered:
            raise ValidationError(
                "destination exists on disk but is not a registered worktree",
                kind="topic_dest_recovery",
                details={"path": str(dest)},
            )

        now = utc_now_iso()
        if existing is not None:
            if existing["branch_name"] != branch_name or (
                agent_name
                and existing["agent_name"]
                and existing["agent_name"] != agent_name
            ):
                raise ValidationError(
                    "topic already exists with different agent/branch",
                    kind="topic_exists_conflict",
                    details={"topic_id": existing["id"]},
                )
            stored = (
                normalize_path(existing["worktree_path"], label="worktree_path")
                if existing["worktree_path"]
                else None
            )
            listed = registered.get(dest_key) or (
                registered.get(stored) if stored else None
            )
            if listed is None:
                raise ValidationError(
                    "topic row exists but git worktree list does not match; recovery",
                    kind="topic_dest_recovery",
                    details={"topic_id": existing["id"], "path": str(dest)},
                )
            tid = str(existing["id"])
            if agent_name and not existing["agent_name"]:
                conn.execute(
                    """
                    UPDATE topics
                    SET agent_name = ?, updated_at = ?
                    WHERE id = ? AND project_name = ?
                    """,
                    (agent_name, utc_now_iso(), tid, project),
                )
                conn.commit()
            created_wt = False
        else:
            tid = _new_id("topic")
            plan_path = None
            if brief:
                plan_path = f"brief:{(brief.get('plan_path') or '')}"
            with immediate_transaction(conn) as c:
                c.execute(
                    """
                    INSERT INTO topics(
                      id, project_name, name, title, goal,
                      coordinator_session_id, coordinator_generation,
                      branch_name, worktree_path, active_run_id, plan_path,
                      lifecycle_state, result_state, agent_name,
                      last_step, created_at, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?, 'proposed', ?, ?, 'record', ?, ?)
                    """,
                    (
                        tid,
                        project,
                        name,
                        title,
                        goal,
                        coord["id"],
                        int(coord["generation"]),
                        branch_name,
                        str(dest.resolve()),
                        None,
                        plan_path,
                        "planning" if brief else "none",
                        agent_name,
                        now,
                        now,
                    ),
                )

            from orch.commands.worktree_add import worktree_add_unlocked

            added = worktree_add_unlocked(
                project, agent_name or "topic", branch_name, dest=dest
            )
            with immediate_transaction(conn) as c:
                c.execute(
                    """
                    UPDATE topics
                    SET base_commit = ?, worktree_path = ?, last_step = 'worktree',
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (added["base_sha"], added["worktree_path"], utc_now_iso(), tid),
                )
            created_wt = True

        run_payload: dict[str, Any] | None = None
        if provision_session:
            if not agent_name:
                raise ValidationError(
                    "provision_session requires --agent",
                    kind="topic_agent_required",
                )
            from orch.runtime.lifecycle import AgentLifecycleService

            blocking = _blocking_topic_runs(conn, tid)
            running = [r for r in blocking if r["state"] == "running"]
            other = [r for r in blocking if r["state"] != "running"]
            if other:
                raise ValidationError(
                    "topic still has a live run; stop or reconcile before starting another",
                    kind="topic_run_active",
                    details={
                        "run_id": other[0]["id"],
                        "state": other[0]["state"],
                    },
                )
            if running:
                run_payload = {"run_id": running[0]["id"], "reused": True}
            else:
                svc = AgentLifecycleService(project)
                run_payload = svc.start_unlocked(
                    conn,
                    agent=agent_name,
                    branch=branch_name,
                    worktree_path=str(dest.resolve()),
                    topic_id=tid,
                )
            run_id = (run_payload.get("run") or {}).get("id") or run_payload.get(
                "run_id"
            )
            with immediate_transaction(conn) as c:
                c.execute(
                    """
                    UPDATE topics
                    SET active_run_id = ?, lifecycle_state = 'active',
                        last_step = 'agent', result_state = 'implementing',
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (run_id, utc_now_iso(), tid),
                )

        row = conn.execute("SELECT * FROM topics WHERE id = ?", (tid,)).fetchone()
        if not provision_session and row["lifecycle_state"] == "active":
            raise ValidationError(
                "refusing to mark topic active without a session",
                kind="topic_session_required",
            )
        reg = load_registry() or {}
        locator = attach_locator(
            base_url=str(reg.get("base_url") or "http://127.0.0.1:4096"),
            worktree_path=str(dest.resolve()),
            session_id=None,
        )
        return {
            "topic": {k: row[k] for k in row.keys()},
            "coordinator_id": coord["id"],
            "attach": locator,
            "brief": brief or {},
            "created_worktree": created_wt,
            "run": run_payload,
        }
    finally:
        conn.close()
        release(lock)


def topic_list(project: str, *, include_archived: bool = False) -> dict[str, Any]:
    conn = open_project_db(project, init=True)
    try:
        if include_archived:
            rows = conn.execute(
                "SELECT * FROM topics WHERE project_name = ? ORDER BY updated_at DESC",
                (project,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM topics
                WHERE project_name = ? AND lifecycle_state != 'archived'
                ORDER BY updated_at DESC
                """,
                (project,),
            ).fetchall()
        return {"topics": [{k: r[k] for k in r.keys()} for r in rows]}
    finally:
        conn.close()


def topic_show(project: str, topic_id: str) -> dict[str, Any]:
    conn = open_project_db(project, init=True)
    try:
        row = conn.execute(
            "SELECT * FROM topics WHERE id = ? AND project_name = ?",
            (topic_id, project),
        ).fetchone()
        if row is None:
            raise ValidationError(f"unknown topic: {topic_id}", kind="topic_not_found")
        topic = {k: row[k] for k in row.keys()}
        run = None
        if topic.get("active_run_id"):
            run = get_run(conn, topic["active_run_id"])
        coord = conn.execute(
            "SELECT * FROM coordinator_sessions WHERE id = ?",
            (topic["coordinator_session_id"],),
        ).fetchone()
        return {
            "topic": topic,
            "active_run": run,
            "coordinator": {k: coord[k] for k in coord.keys()} if coord else None,
        }
    finally:
        conn.close()


def topic_open(
    project: str,
    topic_id: str,
    *,
    fork: bool = False,
    launch: bool = False,
) -> dict[str, Any]:
    shown = topic_show(project, topic_id)
    run_id = (shown.get("topic") or {}).get("active_run_id")
    if run_id:
        if fork:
            return fork_inspect(project, run_id)
        return agent_open(project, run_id, fork=False, launch=launch)
    reg = load_registry() or {}
    wt = shown["topic"]["worktree_path"]
    locator = attach_locator(
        base_url=str(reg.get("base_url") or "http://127.0.0.1:4096"),
        worktree_path=wt,
        session_id=None,
    )
    return {
        "mode": "topic_open",
        "topic_id": topic_id,
        "attach": locator,
        "launched": False,
        "note": "no active_run_id; attach locator is directory-only",
    }


def _task_status_for_topic(
    conn: sqlite3.Connection, row: sqlite3.Row
) -> str | None:
    if not row["task_id"]:
        return None
    task = conn.execute(
        "SELECT status FROM tasks WHERE id = ?", (row["task_id"],)
    ).fetchone()
    return str(task["status"]) if task is not None else None


def _assert_topic_ready_lifecycle(row: sqlite3.Row) -> None:
    if row["lifecycle_state"] not in ("proposed", "active", "ready", "enqueued"):
        raise ValidationError(
            f"cannot mark ready from {row['lifecycle_state']}",
            kind="topic_ready_illegal",
            details={"lifecycle_state": row["lifecycle_state"]},
        )


def _assert_topic_not_ready_frozen(conn: sqlite3.Connection, row: sqlite3.Row) -> None:
    if row["lifecycle_state"] != "enqueued":
        return
    status = _task_status_for_topic(conn, row)
    if status in ("pending", "merging") or status is None:
        raise ValidationError(
            "topic SHA is frozen after enqueue; retry is the only unfreeze path",
            kind="topic_sha_frozen",
            details={
                "topic_id": row["id"],
                "task_id": row["task_id"],
                "task_status": status,
            },
        )


def _assert_ready_git_evidence(
    project: str, row: sqlite3.Row, commit_sha: str
) -> str:
    root = get_project_path(project)
    bare = root / BARE_DIR_NAME
    wt = Path(row["worktree_path"])
    registered = _registered_worktrees(bare)
    if wt.resolve() not in registered:
        raise ValidationError(
            "topic worktree is not registered in git worktree list",
            kind="topic_worktree_unregistered",
            details={"path": str(wt)},
        )
    head_br = run_git_worktree(
        ["rev-parse", "--abbrev-ref", "HEAD"], wt, check=True
    ).stdout.strip()
    if head_br != row["branch_name"]:
        raise ValidationError(
            "worktree HEAD branch does not match topic branch",
            kind="topic_branch_mismatch",
            details={"head": head_br, "branch": row["branch_name"]},
        )
    porcelain = run_git_worktree(["status", "--porcelain"], wt, check=True)
    if porcelain.stdout.strip():
        raise ValidationError(
            "topic worktree is dirty",
            kind="topic_worktree_dirty",
            details={"porcelain": porcelain.stdout},
        )
    head = run_git_worktree(["rev-parse", "HEAD"], wt, check=True).stdout.strip()
    if commit_sha != head:
        raise ValidationError(
            "verification commit is not worktree HEAD",
            kind="topic_verification_sha_mismatch",
            details={"commit_sha": commit_sha, "head": head},
        )
    count_r = run_git_ref(
        ["rev-list", "--count", f"{TARGET_BRANCH}..{head}"],
        bare,
        check=True,
    )
    try:
        n = int(count_r.stdout.strip())
    except ValueError:
        n = 0
    if n <= 0:
        raise ValidationError(
            "topic has no commits ahead of develop",
            kind="topic_empty_diff",
            details={"head": head, "count": n},
        )
    return head


def topic_ready(
    project: str,
    topic_id: str,
    *,
    verification: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Record verification evidence and mark ready_for_enqueue.
    Does NOT enqueue or merge.
    """
    lock = acquire(project_lock_path(project), command="topic-ready", project=project)
    conn = open_project_db(project, init=True)
    try:
        row = conn.execute(
            "SELECT * FROM topics WHERE id = ? AND project_name = ?",
            (topic_id, project),
        ).fetchone()
        if row is None:
            raise ValidationError(f"unknown topic: {topic_id}", kind="topic_not_found")
        verification = verification or {}
        required = ("commands", "commit_sha")
        missing = [k for k in required if k not in verification]
        if missing:
            raise ValidationError(
                f"verification record missing: {missing}",
                kind="topic_verification_incomplete",
            )
        commit_sha = str(verification["commit_sha"]).strip()
        commands = verification["commands"]
        if not isinstance(commands, list) or not commands:
            raise ValidationError(
                "verification.commands must be a non-empty list",
                kind="topic_verification_incomplete",
            )
        _assert_topic_ready_lifecycle(row)
        _assert_topic_not_ready_frozen(conn, row)
        _assert_ready_git_evidence(project, row, commit_sha)
        now = utc_now_iso()
        from orch.verification.service import create_from_topic_ready

        record = create_from_topic_ready(
            conn,
            project=project,
            topic_id=topic_id,
            commit_sha=commit_sha,
            commands=[str(c) for c in commands],
            created_by="topic-ready",
            results=verification.get("results"),
        )
        keep_enqueued = row["lifecycle_state"] == "enqueued"
        new_lifecycle = "enqueued" if keep_enqueued else "ready"
        conn.execute(
            """
            UPDATE topics
            SET lifecycle_state = ?,
                result_state = 'ready_for_enqueue',
                verification_record_id = ?,
                last_step = 'ready',
                updated_at = ?
            WHERE id = ?
            """,
            (new_lifecycle, record["id"], now, topic_id),
        )
        conn.commit()
        return {
            "topic_id": topic_id,
            "lifecycle_state": new_lifecycle,
            "result_state": "ready_for_enqueue",
            "verification": {
                "commit_sha": commit_sha,
                "commands": list(commands),
                "record_id": record["id"],
            },
            "verification_record_id": record["id"],
            "verification_record": {
                "id": record["id"],
                "scope": record["scope"],
                "commit_sha": record["commit_sha"],
                "status": record["status"],
                "expires_at": record["expires_at"],
            },
            "enqueued": False,
        }
    finally:
        conn.close()
        release(lock)


def topic_archive(project: str, topic_id: str) -> dict[str, Any]:
    lock = acquire(
        project_lock_path(project), command="topic-archive", project=project
    )
    conn = open_project_db(project, init=True)
    try:
        row = conn.execute(
            "SELECT * FROM topics WHERE id = ? AND project_name = ?",
            (topic_id, project),
        ).fetchone()
        if row is None:
            raise ValidationError(f"unknown topic: {topic_id}", kind="topic_not_found")
        if row["lifecycle_state"] == "enqueued":
            task = conn.execute(
                "SELECT status FROM tasks WHERE id = ?",
                (row["task_id"],),
            ).fetchone()
            if task is not None and task["status"] in (
                "pending",
                "merging",
                "conflict",
                "recovery_required",
            ):
                raise ValidationError(
                    "cannot archive topic with an active queue task",
                    kind="topic_enqueue_active",
                    details={"task_id": row["task_id"]},
                )
        now = utc_now_iso()
        conn.execute(
            """
            UPDATE topics
            SET lifecycle_state = 'archived', archived_at = ?, updated_at = ?,
                last_step = 'archive'
            WHERE id = ? AND project_name = ?
            """,
            (now, now, topic_id, project),
        )
        conn.commit()
        return {"topic_id": topic_id, "archived": True}
    finally:
        conn.close()
        release(lock)


def topic_enqueue(project: str, topic_id: str, *, priority: int = 1) -> dict[str, Any]:
    """Feed existing merge queue. Does not merge."""
    lock = acquire(
        project_lock_path(project), command="topic-enqueue", project=project
    )
    conn = open_project_db(project, init=True)
    try:
        row = conn.execute(
            "SELECT * FROM topics WHERE id = ? AND project_name = ?",
            (topic_id, project),
        ).fetchone()
        if row is None:
            raise ValidationError(f"unknown topic: {topic_id}", kind="topic_not_found")
        if row["lifecycle_state"] == "enqueued" and row["task_id"]:
            task = conn.execute(
                "SELECT * FROM tasks WHERE id = ?", (row["task_id"],)
            ).fetchone()
            if task is not None and task["status"] in (
                "pending",
                "merging",
                "conflict",
                "recovery_required",
            ):
                return {
                    "enqueued": True,
                    "task_id": task["id"],
                    "source_commit": task["source_commit"],
                    "topic": {k: row[k] for k in row.keys()},
                    "idempotent": True,
                }
        if row["lifecycle_state"] not in ("ready", "enqueued"):
            raise ValidationError(
                f"topic-enqueue requires ready; got {row['lifecycle_state']}",
                kind="topic_not_ready",
                details={"lifecycle_state": row["lifecycle_state"]},
            )
        agent = row["agent_name"]
        if not agent:
            raise ValidationError(
                "topic has no agent_name; cannot enqueue",
                kind="topic_agent_required",
            )
        vid = row["verification_record_id"]
        if not vid:
            raise ValidationError(
                "topic has no verification record; run topic-ready first",
                kind="topic_verification_incomplete",
            )
        rec = conn.execute(
            "SELECT commit_sha, status FROM verification_records WHERE id = ?",
            (vid,),
        ).fetchone()
        if rec is None or rec["status"] != "passed":
            raise ValidationError(
                "topic verification record is missing or not passed",
                kind="topic_verification_incomplete",
                details={"verification_record_id": vid},
            )
        from orch.commands.enqueue import enqueue_unlocked

        result = enqueue_unlocked(
            conn,
            project,
            agent,
            row["branch_name"],
            row["worktree_path"],
            priority=priority,
            topic_id=topic_id,
            expected_source=str(rec["commit_sha"]),
        )
        fresh = conn.execute(
            "SELECT * FROM topics WHERE id = ?", (topic_id,)
        ).fetchone()
        return {
            "enqueued": True,
            "task_id": result["task_id"],
            "source_commit": result["source_commit"],
            "topic": {k: fresh[k] for k in fresh.keys()},
            "idempotent": False,
        }
    finally:
        conn.close()
        release(lock)


def topic_abandon(project: str, topic_id: str) -> dict[str, Any]:
    lock = acquire(
        project_lock_path(project), command="topic-abandon", project=project
    )
    conn = open_project_db(project, init=True)
    try:
        row = conn.execute(
            "SELECT * FROM topics WHERE id = ? AND project_name = ?",
            (topic_id, project),
        ).fetchone()
        if row is None:
            raise ValidationError(f"unknown topic: {topic_id}", kind="topic_not_found")
        if row["lifecycle_state"] not in ("proposed", "active", "ready"):
            raise ValidationError(
                f"cannot abandon topic in {row['lifecycle_state']}",
                kind="topic_abandon_illegal",
                details={"lifecycle_state": row["lifecycle_state"]},
            )
        now = utc_now_iso()
        conn.execute(
            """
            UPDATE topics
            SET lifecycle_state = 'cancelled', last_step = 'abandon',
                updated_at = ?
            WHERE id = ?
            """,
            (now, topic_id),
        )
        conn.commit()
        return {"topic_id": topic_id, "lifecycle_state": "cancelled", "abandoned": True}
    finally:
        conn.close()
        release(lock)
