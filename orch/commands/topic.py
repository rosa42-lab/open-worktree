"""Topic + coordinator product workflow (V12-015) — core commands."""

from __future__ import annotations

import uuid
from typing import Any

from orch.agent_repo import attach_locator, get_run
from orch.constants import project_lock_path
from orch.db import open_project_db
from orch.errors import ValidationError
from orch.locks import acquire, release
from orch.runtime.registry import load_registry
from orch.runtime.takeover import agent_open, fork_inspect
from orch.util import utc_now_iso


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


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
    worktree_path: str,
    brief: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create topic bound to active coordinator. Does not start worker by itself."""
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
        tid = _new_id("topic")
        now = utc_now_iso()
        conn.execute(
            """
            INSERT INTO topics(
              id, project_name, name, title, goal,
              coordinator_session_id, coordinator_generation,
              branch_name, worktree_path, active_run_id, plan_path,
              lifecycle_state, result_state, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?, NULL, NULL, 'proposed', 'none', ?, ?)
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
                worktree_path,
                now,
                now,
            ),
        )
        conn.commit()
        # Persist brief as plan_path optional JSON sidecar path note in title? store in plan_path field as marker
        if brief:
            conn.execute(
                "UPDATE topics SET plan_path = ?, result_state = 'planning', updated_at = ? WHERE id = ?",
                (f"brief:{brief.get('plan_path') or ''}", now, tid),
            )
            conn.commit()
        row = conn.execute("SELECT * FROM topics WHERE id = ?", (tid,)).fetchone()
        reg = load_registry() or {}
        locator = attach_locator(
            base_url=str(reg.get("base_url") or "http://127.0.0.1:4096"),
            worktree_path=worktree_path,
            session_id=None,
        )
        return {
            "topic": {k: row[k] for k in row.keys()},
            "coordinator_id": coord["id"],
            "attach": locator,
            "brief": brief or {},
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
    # No run yet — return worktree locator only
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
        conn.execute(
            """
            UPDATE topics
            SET lifecycle_state = 'ready',
                result_state = 'ready_for_enqueue',
                updated_at = ?
            WHERE id = ?
            """,
            (now, topic_id),
        )
        conn.commit()
        return {
            "topic_id": topic_id,
            "lifecycle_state": "ready",
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
        now = utc_now_iso()
        cur = conn.execute(
            """
            UPDATE topics
            SET lifecycle_state = 'archived', archived_at = ?, updated_at = ?
            WHERE id = ? AND project_name = ?
            """,
            (now, now, topic_id, project),
        )
        if cur.rowcount == 0:
            raise ValidationError(f"unknown topic: {topic_id}", kind="topic_not_found")
        conn.commit()
        return {"topic_id": topic_id, "archived": True}
    finally:
        conn.close()
        release(lock)
