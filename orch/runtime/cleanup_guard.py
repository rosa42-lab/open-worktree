"""Runtime-aware cleanup guards (V12-011)."""

from __future__ import annotations

import sqlite3
from typing import Any

from orch.agent_state import CLEANUP_BLOCKING_LIFECYCLE
from orch.runtime.lease import get_lease, lease_expired


def runtime_prune_blockers(
    conn: sqlite3.Connection,
    *,
    worktree_path: str,
    task_status: str | None = None,
) -> list[dict[str, Any]]:
    """
    Return list of blockers. Empty means runtime guards pass.
    Does not weaken Git gauntlet — caller still runs v1.1 checks after.
    """
    blockers: list[dict[str, Any]] = []
    if task_status and task_status != "merged":
        blockers.append(
            {
                "code": "task_not_merged",
                "detail": f"linked task status={task_status}",
            }
        )
        # skipped also blocks auto prune
        if task_status == "skipped":
            blockers.append(
                {
                    "code": "task_skipped_retained",
                    "detail": "skipped tasks are retained by policy",
                }
            )

    rows = conn.execute(
        """
        SELECT * FROM agent_runs
        WHERE worktree_path = ?
        """,
        (worktree_path,),
    ).fetchall()
    for row in rows:
        d = {k: row[k] for k in row.keys()}
        state = d.get("state")
        if state in CLEANUP_BLOCKING_LIFECYCLE:
            blockers.append(
                {
                    "code": "active_or_unresolved_run",
                    "detail": f"run {d['id']} state={state}",
                    "run_id": d["id"],
                }
            )
        if state not in ("exited", "archived") and d.get("archived_at") is None:
            if state not in CLEANUP_BLOCKING_LIFECYCLE:
                # still not archived
                blockers.append(
                    {
                        "code": "run_not_archived",
                        "detail": f"run {d['id']} state={state} not archived",
                        "run_id": d["id"],
                    }
                )
        lease = get_lease(conn, d["id"])
        if lease and not lease_expired(lease):
            blockers.append(
                {
                    "code": "active_lease",
                    "detail": f"run {d['id']} has non-expired lease "
                    f"controller={lease.get('controller')}",
                    "run_id": d["id"],
                }
            )
        if d.get("controller") == "human":
            blockers.append(
                {
                    "code": "human_controlled",
                    "detail": f"run {d['id']} still human-controlled",
                    "run_id": d["id"],
                }
            )
    return blockers
