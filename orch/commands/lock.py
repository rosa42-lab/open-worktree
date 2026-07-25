"""lock-status / lock-break."""

from __future__ import annotations

from typing import Any

from orch.constants import project_lock_path
from orch.db import open_project_db
from orch.locks import lock_break, lock_status
from orch.validate import validate_project_name


def cmd_lock_status(project: str) -> dict[str, Any]:
    project = validate_project_name(project)
    return lock_status(project_lock_path(project))


def cmd_lock_break(project: str, *, force: bool) -> dict[str, Any]:
    project = validate_project_name(project)
    conn = None
    db_path = project_lock_path(project).parent / "orchestrator.db"
    if db_path.exists():
        conn = open_project_db(project, init=False)
    try:
        return lock_break(
            project_lock_path(project),
            force=force,
            audit_conn=conn,
            project=project,
        )
    finally:
        if conn is not None:
            conn.close()
