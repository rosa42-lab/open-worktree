"""orch <project> worktree-add."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from orch.constants import (
    BARE_DIR_NAME,
    TARGET_BRANCH,
    WORKTREES_DIR_NAME,
    project_lock_path,
)
from orch.db import open_project_db
from orch.errors import OrchError, ExitCode, UsageError, ValidationError
from orch.git.parser import check_ref_format_branch
from orch.git.ref import run_git_ref
from orch.git.worktree import worktree_list_porcelain
from orch.locks import acquire, release
from orch.registry import get_project_path
from orch.validate import branch_safe_name, validate_agent_name, validate_project_name


def cmd_worktree_add(
    project: str,
    agent: str,
    branch: str,
    *,
    base: str = TARGET_BRANCH,
) -> dict[str, Any]:
    project = validate_project_name(project)
    agent = validate_agent_name(agent)
    check_ref_format_branch(branch)
    if base != TARGET_BRANCH:
        raise UsageError(
            f"v1.1 only allows --base {TARGET_BRANCH}",
            details={"base": base},
        )

    root = get_project_path(project)
    bare = root / BARE_DIR_NAME
    safe = branch_safe_name(branch)
    dest = root / WORKTREES_DIR_NAME / f"{agent}-{safe}"
    if dest.exists():
        raise ValidationError(
            f"worktree path already exists: {dest}",
            kind="worktree_exists",
            details={"path": str(dest)},
        )

    conn = open_project_db(project, init=True)
    handle = None
    try:
        handle = acquire(
            project_lock_path(project),
            command="worktree-add",
            project=project,
            audit_conn=conn,
        )
        (root / WORKTREES_DIR_NAME).mkdir(parents=True, exist_ok=True)

        exists = run_git_ref(["rev-parse", "--verify", f"refs/heads/{branch}"], bare)
        if exists.ok:
            # branch already checked out elsewhere?
            for entry in worktree_list_porcelain(bare):
                b = entry.get("branch", "")
                if b.endswith(f"/{branch}") or b == f"refs/heads/{branch}":
                    raise ValidationError(
                        f"branch {branch} already checked out in another worktree",
                        kind="branch_checked_out",
                        details={"worktree": entry.get("worktree"), "branch": branch},
                    )
            r = run_git_ref(
                ["worktree", "add", str(dest), branch],
                bare,
            )
        else:
            r = run_git_ref(
                ["worktree", "add", "-b", branch, str(dest), base],
                bare,
            )
        if not r.ok:
            raise OrchError(
                f"worktree add failed: {r.stderr.strip()}",
                code=ExitCode.GIT,
                kind="git_failure",
                details={"stderr": r.stderr, "stdout": r.stdout},
            )
        return {
            "project": project,
            "agent": agent,
            "branch": branch,
            "worktree_path": str(dest.resolve()),
            "base": base,
            "created_branch": not exists.ok,
        }
    finally:
        if handle is not None:
            release(handle, audit_conn=conn)
        conn.close()
