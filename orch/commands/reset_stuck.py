"""orch <project> reset-stuck."""

from __future__ import annotations

from typing import Any

from orch.constants import BARE_DIR_NAME, project_lock_path
from orch.db import open_project_db
from orch.locks import acquire, release
from orch.merge.recover import recover_task
from orch.registry import get_project_path
from orch.task_resolve import row_to_dict
from orch.validate import validate_project_name


def cmd_reset_stuck(project: str) -> dict[str, Any]:
    project = validate_project_name(project)
    root = get_project_path(project)
    bare = (root / BARE_DIR_NAME).resolve()
    conn = open_project_db(project, init=True)
    handle = None
    try:
        handle = acquire(
            project_lock_path(project),
            command="reset-stuck",
            project=project,
            audit_conn=conn,
        )
        rows = conn.execute(
            """
            SELECT * FROM tasks
            WHERE status IN ('merging', 'recovery_required')
            ORDER BY claimed_at ASC
            """
        ).fetchall()
        if not rows:
            return {"recovered": [], "message": "no stuck tasks"}
        results = []
        for row in rows:
            task = row_to_dict(row)
            results.append(recover_task(conn, root, bare, task))
        return {"recovered": results}
    finally:
        if handle is not None:
            release(handle, audit_conn=conn)
        conn.close()
