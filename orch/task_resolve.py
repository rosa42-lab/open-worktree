"""Resolve task_id or branch to a task row (task T-0302)."""

from __future__ import annotations

import sqlite3
from typing import Any

from orch.errors import UsageError, ValidationError
from orch.git.parser import check_ref_format_branch
from orch.state_machine import ACTIVE_STATUSES


def resolve_task(conn: sqlite3.Connection, token: str) -> sqlite3.Row:
    if not token:
        raise UsageError("task id or branch is required")

    # Exact primary key first — never guess UUID by shape alone without DB hit.
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (token,)).fetchone()
    if row is not None:
        return row

    # Branch path requires check-ref-format
    try:
        check_ref_format_branch(token)
    except (UsageError, ValidationError) as exc:
        raise UsageError(
            f"not a known task id and not a valid branch name: {token!r}",
            details={"token": token, "reason": str(exc)},
        ) from exc

    rows = conn.execute(
        "SELECT * FROM tasks WHERE branch_name = ? ORDER BY submitted_at DESC, queue_seq DESC",
        (token,),
    ).fetchall()
    if not rows:
        raise UsageError(
            f"no task found for branch {token!r}",
            details={"branch": token},
        )
    active = [r for r in rows if r["status"] in ACTIVE_STATUSES]
    if len(active) == 1:
        return active[0]
    if len(active) > 1:
        raise UsageError(
            f"ambiguous active tasks for branch {token!r}",
            details={"task_ids": [r["id"] for r in active]},
        )
    # only terminal history
    if len(rows) == 1:
        return rows[0]
    raise UsageError(
        f"ambiguous historical tasks for branch {token!r}; pass task id",
        details={"task_ids": [r["id"] for r in rows]},
    )


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}
