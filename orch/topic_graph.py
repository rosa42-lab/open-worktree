"""Topic identity graph (V18). Application-layer; no topic_events."""

from __future__ import annotations

import sqlite3
from typing import Any

from orch.errors import ValidationError
from orch.util import utc_now_iso
from orch.validate import canonical_worktree_path

_LIVE_RUN = "state NOT IN ('exited', 'archived')"
_ENQUEUED_TASK = ("pending", "merging", "conflict", "recovery_required")
_TERMINAL_TASK = ("merged", "skipped")


def _conflict(message: str, **details: Any) -> None:
    raise ValidationError(
        message,
        kind="topic_graph_conflict",
        details=details,
    )


def _live_runs(conn: sqlite3.Connection, topic_id: str) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            f"""
            SELECT * FROM agent_runs
            WHERE topic_id = ? AND {_LIVE_RUN}
            """,
            (topic_id,),
        ).fetchall()
    )


def assert_topic_graph(conn: sqlite3.Connection, topic_id: str) -> None:
    row = conn.execute("SELECT * FROM topics WHERE id = ?", (topic_id,)).fetchone()
    if row is None:
        _conflict("topic row missing", topic_id=topic_id)
    topic_id = str(row["id"])
    project = str(row["project_name"])
    lifecycle = str(row["lifecycle_state"])
    task_id = row["task_id"]
    stored_path = str(row["worktree_path"] or "")
    if stored_path:
        canon = canonical_worktree_path(stored_path, label="worktree_path")
        if canon != stored_path:
            _conflict(
                "worktree_path is not canonical",
                topic_id=topic_id,
                stored=stored_path,
                canonical=canon,
            )

    task = None
    if task_id:
        task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()

    live = _live_runs(conn, topic_id)
    active_run_id = row["active_run_id"]

    if lifecycle == "proposed":
        if task_id:
            _conflict("proposed topic must not have task_id", topic_id=topic_id)
        if live:
            _conflict("proposed topic must not have a live run", topic_id=topic_id)
    elif lifecycle == "active":
        if task_id:
            _conflict("active topic must not have task_id", topic_id=topic_id)
        if not live:
            _conflict("active topic requires a live run", topic_id=topic_id)
    elif lifecycle == "ready":
        if task_id:
            _conflict("ready topic must not have task_id", topic_id=topic_id)
    elif lifecycle == "enqueued":
        if not task_id or task is None:
            _conflict("enqueued topic requires a task", topic_id=topic_id)
        elif str(task["status"]) not in _ENQUEUED_TASK:
            _conflict(
                "enqueued topic task status not allowed",
                topic_id=topic_id,
                status=task["status"],
            )
        if task is not None and str(task["agent_name"]) != str(row["agent_name"] or ""):
            _conflict("enqueued agent_name mismatch", topic_id=topic_id)
        if task is not None and str(task["branch_name"]) != str(row["branch_name"]):
            _conflict("enqueued branch mismatch", topic_id=topic_id)
    elif lifecycle == "merged":
        if not task_id or task is None or str(task["status"]) != "merged":
            _conflict("merged topic requires merged task", topic_id=topic_id)
        if live:
            _conflict("merged topic must not have a live run", topic_id=topic_id)
    elif lifecycle == "rejected":
        if task_id:
            _conflict("rejected topic must clear task_id", topic_id=topic_id)
        if live:
            _conflict("rejected topic must not have a live run", topic_id=topic_id)
    elif lifecycle == "cancelled":
        if task_id:
            _conflict("cancelled topic must not have task_id", topic_id=topic_id)
        if live:
            _conflict("cancelled topic must not have a live run", topic_id=topic_id)
    elif lifecycle == "archived":
        if live:
            _conflict("archived topic must not have a live run", topic_id=topic_id)
        if task_id and task is not None and str(task["status"]) not in _TERMINAL_TASK:
            _conflict(
                "archived topic cannot point at an active task",
                topic_id=topic_id,
                status=task["status"],
            )
        if task_id and task is not None and str(task["status"]) in (
            "pending",
            "merging",
            "conflict",
            "recovery_required",
        ):
            _conflict("archived topic has active queue task", topic_id=topic_id)
    else:
        _conflict("unknown lifecycle", topic_id=topic_id, lifecycle=lifecycle)

    if active_run_id:
        run = conn.execute(
            "SELECT * FROM agent_runs WHERE id = ?", (active_run_id,)
        ).fetchone()
        if run is None:
            _conflict("active_run_id missing run", topic_id=topic_id, run_id=active_run_id)
        if str(run["project_name"]) != project:
            _conflict("active run project mismatch", topic_id=topic_id)
        if str(run["state"]) not in ("exited", "archived"):
            if run["topic_id"] not in (topic_id, None) and str(run["topic_id"]) != topic_id:
                _conflict(
                    "active_run_id does not bind topic_id",
                    topic_id=topic_id,
                    run_id=active_run_id,
                )
            if run["topic_id"] is not None and str(run["topic_id"]) != topic_id:
                _conflict("run.topic_id != topic.id", topic_id=topic_id)

    for run in live:
        if str(run["project_name"]) != project:
            _conflict("live run project mismatch", topic_id=topic_id, run_id=run["id"])
        if active_run_id and str(active_run_id) != str(run["id"]) and len(live) == 1:
            _conflict(
                "active_run_id does not match live run",
                topic_id=topic_id,
                active_run_id=active_run_id,
                run_id=run["id"],
            )


def detach_live_runs(conn: sqlite3.Connection, topic_id: str) -> None:
    now = utc_now_iso()
    conn.execute(
        f"""
        UPDATE agent_runs
        SET topic_id = NULL, updated_at = ?
        WHERE topic_id = ? AND {_LIVE_RUN}
        """,
        (now, topic_id),
    )
    conn.execute(
        "UPDATE topics SET active_run_id = NULL WHERE id = ?",
        (topic_id,),
    )


def doctor_report(conn: sqlite3.Connection, project: str) -> dict[str, list[dict[str, Any]]]:
    conflicts: list[dict[str, Any]] = []
    orphans: list[dict[str, Any]] = []
    recovery: list[dict[str, Any]] = []
    topics = conn.execute(
        "SELECT id, active_run_id, task_id, provision_phase FROM topics WHERE project_name = ?",
        (project,),
    ).fetchall()
    for topic in topics:
        tid = str(topic["id"])
        try:
            assert_topic_graph(conn, tid)
        except ValidationError as exc:
            if exc.kind == "topic_graph_conflict":
                conflicts.append(
                    {"topic_id": tid, "kind": exc.kind, "message": exc.message}
                )
        if topic["active_run_id"]:
            run = conn.execute(
                "SELECT id FROM agent_runs WHERE id = ?",
                (topic["active_run_id"],),
            ).fetchone()
            if run is None:
                orphans.append(
                    {
                        "id": tid,
                        "kind": "missing_run",
                        "active_run_id": topic["active_run_id"],
                    }
                )
        if topic["task_id"]:
            task = conn.execute(
                "SELECT id FROM tasks WHERE id = ?", (topic["task_id"],)
            ).fetchone()
            if task is None:
                orphans.append(
                    {"id": tid, "kind": "missing_task", "task_id": topic["task_id"]}
                )
        if topic["provision_phase"] == "needs_recovery":
            recovery.append({"topic_id": tid, "provision_phase": "needs_recovery"})
    return {
        "conflicts": conflicts,
        "orphans": orphans,
        "provision_needs_recovery": recovery,
    }
