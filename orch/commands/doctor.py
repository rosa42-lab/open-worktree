"""orch <project> doctor — read-only diagnose (V17). Does not migrate."""

from __future__ import annotations

from typing import Any

from orch.constants import project_db_path
from orch.db import open_project_db
from orch.errors import ValidationError
from orch.migrations import classify_db, user_version
from orch.validate import validate_project_name


def cmd_doctor(project: str) -> dict[str, Any]:
    project = validate_project_name(project)
    db_path = project_db_path(project)
    if not db_path.exists():
        raise ValidationError(
            f"project database is not initialized: {db_path}",
            kind="database_not_initialized",
        )
    conn = open_project_db(project, init=False)
    try:
        return {
            "schema": {
                "user_version": user_version(conn),
                "classify": classify_db(conn),
            },
            "conflicts": [],
            "orphans": [],
            "provision_needs_recovery": [],
        }
    finally:
        conn.close()
