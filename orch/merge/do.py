"""Merge Do stage — git merge --no-ff source_commit."""

from __future__ import annotations

import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from orch.audit import write_audit
from orch.constants import MAIN_WORKTREE_NAME
from orch.db import immediate_transaction
from orch.git._runner import GitResult
from orch.git.worktree import popen_git_worktree, run_git_worktree


def audit_merge_started(conn, task_id: str) -> None:
    with immediate_transaction(conn) as c:
        write_audit(c, "merge_started", task_id=task_id, detail={})


def run_merge_no_ff(
    root: Path,
    source_commit: str,
    *,
    interruptible: bool = True,
) -> GitResult:
    """Execute merge using frozen source_commit only (never branch name)."""
    main = root / MAIN_WORKTREE_NAME
    args = ["merge", "--no-ff", "--no-edit", source_commit]
    if not interruptible:
        return run_git_worktree(args, main)

    proc = popen_git_worktree(args, main)
    try:
        stdout, stderr = proc.communicate()
    except KeyboardInterrupt:
        _terminate_proc(proc)
        raise
    return GitResult(
        returncode=proc.returncode if proc.returncode is not None else -1,
        stdout=stdout or "",
        stderr=stderr or "",
        args=["git", *args],
        proc=proc,
    )


def _terminate_proc(proc: subprocess.Popen[str], grace: float = 5.0) -> None:
    if proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            proc.terminate()
        else:
            proc.send_signal(signal.SIGTERM)
    except OSError:
        pass
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.05)
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.wait(timeout=5)
    except Exception:
        pass


def is_merge_conflict(result: GitResult, main: Path) -> bool:
    text = (result.stdout or "") + (result.stderr or "")
    if result.returncode == 0:
        return False
    merge_head = run_git_worktree(["rev-parse", "--git-path", "MERGE_HEAD"], main)
    mh = Path(merge_head.stdout.strip())
    if not mh.is_absolute():
        mh = main / mh
    if not mh.exists():
        return False
    return "CONFLICT" in text.upper() or "conflict" in text.lower()


def capture_conflict_files(main: Path) -> list[str]:
    r = run_git_worktree(
        ["diff", "--name-only", "--diff-filter=U"],
        main,
    )
    if not r.ok:
        return []
    return [ln for ln in r.stdout.splitlines() if ln.strip()]
