"""pending / list / diff / changes / log — read-only."""

from __future__ import annotations

from typing import Any

from orch.constants import BARE_DIR_NAME, TARGET_BRANCH
from orch.db import open_project_db
from orch.git.ref import run_git_ref
from orch.registry import get_project_path
from orch.task_resolve import resolve_task, row_to_dict
from orch.validate import validate_project_name


def _bare(project: str):
    root = get_project_path(project)
    return root / BARE_DIR_NAME


def cmd_pending(project: str) -> dict[str, Any]:
    project = validate_project_name(project)
    bare = _bare(project)
    conn = open_project_db(project, init=True)
    try:
        rows = conn.execute(
            """
            SELECT * FROM tasks
            WHERE status = 'pending'
            ORDER BY priority ASC, submitted_at ASC, queue_seq ASC
            """
        ).fetchall()
        items = []
        for row in rows:
            d = row_to_dict(row)
            source = row["source_commit"]
            stat = run_git_ref(
                ["diff", "--stat", f"{TARGET_BRANCH}...{source}"],
                bare,
            )
            log = run_git_ref(
                ["log", "--oneline", f"{TARGET_BRANCH}..{source}", "-n", "5"],
                bare,
            )
            d["diff_stat"] = stat.stdout
            d["log_oneline"] = log.stdout
            items.append(d)
        return {"tasks": items}
    finally:
        conn.close()


def cmd_list(project: str, *, all_tasks: bool = False) -> dict[str, Any]:
    project = validate_project_name(project)
    conn = open_project_db(project, init=True)
    try:
        if all_tasks:
            rows = conn.execute(
                """
                SELECT * FROM tasks
                ORDER BY submitted_at ASC, queue_seq ASC
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM tasks
                WHERE archived_at IS NULL
                ORDER BY submitted_at ASC, queue_seq ASC
                """
            ).fetchall()
        return {"tasks": [row_to_dict(r) for r in rows], "all": all_tasks}
    finally:
        conn.close()


def cmd_diff(project: str, token: str) -> dict[str, Any]:
    project = validate_project_name(project)
    bare = _bare(project)
    conn = open_project_db(project, init=True)
    try:
        task = resolve_task(conn, token)
        source = task["source_commit"]
        r = run_git_ref(["diff", f"{TARGET_BRANCH}...{source}"], bare)
        return {
            "task_id": task["id"],
            "branch": task["branch_name"],
            "source_commit": source,
            "diff": r.stdout,
            "returncode": r.returncode,
        }
    finally:
        conn.close()


def cmd_changes(project: str, token: str) -> dict[str, Any]:
    project = validate_project_name(project)
    bare = _bare(project)
    conn = open_project_db(project, init=True)
    try:
        task = resolve_task(conn, token)
        source = task["source_commit"]
        names = run_git_ref(
            ["diff", "--name-status", f"{TARGET_BRANCH}...{source}"], bare
        )
        stat = run_git_ref(["diff", "--stat", f"{TARGET_BRANCH}...{source}"], bare)
        log = run_git_ref(["log", f"{TARGET_BRANCH}..{source}"], bare)
        return {
            "task_id": task["id"],
            "branch": task["branch_name"],
            "source_commit": source,
            "name_status": names.stdout,
            "stat": stat.stdout,
            "log": log.stdout,
        }
    finally:
        conn.close()


def cmd_log(project: str, token: str) -> dict[str, Any]:
    project = validate_project_name(project)
    bare = _bare(project)
    conn = open_project_db(project, init=True)
    try:
        task = resolve_task(conn, token)
        source = task["source_commit"]
        r = run_git_ref(["log", f"{TARGET_BRANCH}..{source}"], bare)
        return {
            "task_id": task["id"],
            "branch": task["branch_name"],
            "source_commit": source,
            "log": r.stdout,
        }
    finally:
        conn.close()
