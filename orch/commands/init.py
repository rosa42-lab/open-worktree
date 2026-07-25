"""orch <project> init."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from orch.constants import (
    BARE_DIR_NAME,
    MAIN_WORKTREE_NAME,
    TARGET_BRANCH,
    WORKTREES_DIR_NAME,
    project_lock_path,
)
from orch.db import open_project_db
from orch.errors import OrchError, ExitCode, PrecheckError
from orch.git.ref import run_git_ref
from orch.git.worktree import run_git_worktree, resolve_common_dir
from orch.locks import acquire, release
from orch.registry import get_project_path
from orch.validate import validate_project_name


def cmd_init(project: str) -> dict[str, Any]:
    project = validate_project_name(project)
    root = get_project_path(project)
    bare = root / BARE_DIR_NAME
    if not bare.is_dir():
        raise OrchError(
            f".bare.git missing at {bare}",
            code=ExitCode.GENERAL,
            kind="bare_missing",
            details={"bare": str(bare)},
        )
    r = run_git_ref(["rev-parse", "--is-bare-repository"], bare)
    if not r.ok or r.stdout.strip() != "true":
        raise OrchError(
            f"not a bare repository: {bare}",
            code=ExitCode.GENERAL,
            kind="bare_invalid",
            details={"stderr": r.stderr},
        )
    r = run_git_ref(["show-ref", "--verify", f"refs/heads/{TARGET_BRANCH}"], bare)
    if not r.ok:
        raise OrchError(
            f"branch {TARGET_BRANCH} missing in bare repo",
            code=ExitCode.GENERAL,
            kind="develop_missing",
            details={"stderr": r.stderr},
        )

    # Ensure DB exists before lock audit
    conn = open_project_db(project, init=True)
    handle = None
    try:
        handle = acquire(
            project_lock_path(project),
            command="init",
            project=project,
            audit_conn=conn,
        )
        main = root / MAIN_WORKTREE_NAME
        if main.exists():
            common = resolve_common_dir(main)
            if common != bare.resolve():
                raise PrecheckError(
                    "main/ exists but does not belong to project bare",
                    details={"common": str(common), "bare": str(bare.resolve())},
                )
            br = run_git_worktree(["rev-parse", "--abbrev-ref", "HEAD"], main, check=True)
            if br.stdout.strip() != TARGET_BRANCH:
                raise PrecheckError(
                    f"main/ is not on {TARGET_BRANCH}",
                    details={"head": br.stdout.strip()},
                )
            st = run_git_worktree(["status", "--porcelain"], main, check=True)
            if st.stdout.strip():
                raise PrecheckError(
                    "main/ is not clean",
                    details={"porcelain": st.stdout},
                )
        else:
            wr = run_git_ref(
                ["worktree", "add", str(main), TARGET_BRANCH],
                bare,
            )
            if not wr.ok:
                raise OrchError(
                    f"failed to create main worktree: {wr.stderr}",
                    code=ExitCode.GIT,
                    kind="git_failure",
                    details={"stderr": wr.stderr},
                )

        wt_root = root / WORKTREES_DIR_NAME
        wt_root.mkdir(parents=True, exist_ok=True)

        return {
            "project": project,
            "root": str(root),
            "bare": str(bare.resolve()),
            "main": str(main.resolve()),
            "worktrees": str(wt_root.resolve()),
            "db_initialized": True,
        }
    finally:
        if handle is not None:
            release(handle, audit_conn=conn)
        conn.close()
