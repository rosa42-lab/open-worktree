"""Agent run repository helpers (schema 2). V12-003 / V12-006 register path."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from orch.agent_state import (
    assert_controller,
    assert_desired,
    assert_lifecycle_transition,
    assert_observed,
)
from orch.errors import DbError, ValidationError


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def new_run_id() -> str:
    return f"run_{uuid.uuid4().hex}"


def register_observed_run(
    conn: sqlite3.Connection,
    *,
    project_name: str,
    agent_name: str,
    branch_name: str,
    worktree_path: str,
    session_id: str,
    runtime_server_id: str,
    runtime_kind: str = "opencode",
    desired_state: str = "stopped",
    observed_state: str = "idle",
    state: str = "registered",
    controller: str = "none",
) -> dict[str, Any]:
    """
    Temporary observe-only registration of an existing worktree/session.

    Does not start or control a worker. Used before AgentLifecycleService exists.
    """
    assert_lifecycle_transition(None, state)
    assert_desired(desired_state)
    assert_observed(observed_state)
    assert_controller(controller)

    run_id = new_run_id()
    now = _utcnow()
    try:
        conn.execute(
            """
            INSERT INTO agent_runs (
              id, project_name, agent_name, branch_name, worktree_path,
              task_id, runtime_kind, runtime_server_id, session_id,
              state, desired_state, observed_state, controller,
              controller_generation, created_at, updated_at
            ) VALUES (
              ?, ?, ?, ?, ?,
              NULL, ?, ?, ?,
              ?, ?, ?, ?,
              0, ?, ?
            )
            """,
            (
                run_id,
                project_name,
                agent_name,
                branch_name,
                worktree_path,
                runtime_kind,
                runtime_server_id,
                session_id,
                state,
                desired_state,
                observed_state,
                controller,
                now,
                now,
            ),
        )
        conn.execute(
            "INSERT INTO lifecycle_counters(run_id, value) VALUES (?, 0)",
            (run_id,),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        raise ValidationError(
            f"cannot register agent run: {exc}",
            kind="agent_register_conflict",
            details={"worktree_path": worktree_path, "session_id": session_id},
        ) from exc
    except sqlite3.Error as exc:
        raise DbError(f"register agent run failed: {exc}", details={"error": str(exc)}) from exc

    row = get_run(conn, run_id)
    assert row is not None
    return row


def get_run(conn: sqlite3.Connection, run_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM agent_runs WHERE id = ?", (run_id,)
    ).fetchone()
    return _row_to_dict(row)


def list_runs(
    conn: sqlite3.Connection,
    project_name: str,
    *,
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    if include_archived:
        rows = conn.execute(
            """
            SELECT * FROM agent_runs
            WHERE project_name = ?
            ORDER BY created_at DESC
            """,
            (project_name,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM agent_runs
            WHERE project_name = ? AND state != 'archived'
            ORDER BY created_at DESC
            """,
            (project_name,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows if r is not None]  # type: ignore[misc]


def transition_lifecycle(
    conn: sqlite3.Connection,
    run_id: str,
    to_state: str,
    *,
    bump_generation: bool = False,
) -> dict[str, Any]:
    row = get_run(conn, run_id)
    if row is None:
        raise ValidationError(
            f"unknown run: {run_id}",
            kind="agent_run_not_found",
            details={"run_id": run_id},
        )
    assert_lifecycle_transition(row["state"], to_state)
    now = _utcnow()
    gen = int(row["controller_generation"])
    if bump_generation:
        gen += 1
    try:
        conn.execute(
            """
            UPDATE agent_runs
            SET state = ?, controller_generation = ?, updated_at = ?
            WHERE id = ?
            """,
            (to_state, gen, now, run_id),
        )
        conn.commit()
    except sqlite3.Error as exc:
        raise DbError(f"lifecycle transition failed: {exc}", details={"error": str(exc)}) from exc
    out = get_run(conn, run_id)
    assert out is not None
    return out


def attach_locator(
    *,
    base_url: str,
    worktree_path: str,
    session_id: str | None,
    fork: bool = False,
) -> dict[str, Any]:
    parts = ["opencode", "attach", base_url, "--dir", worktree_path]
    if session_id:
        parts.extend(["--session", session_id])
    if fork:
        parts.append("--fork")
    cmd = " ".join(parts)
    return {
        "base_url": base_url,
        "directory": worktree_path,
        "session_id": session_id,
        "fork": fork,
        "command": cmd,
    }
