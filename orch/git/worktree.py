"""Linked-worktree git commands (cwd = worktree path)."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from orch.git._runner import GitResult, popen_git, run_git
from orch.errors import GitError, ValidationError


def run_git_worktree(
    args: Sequence[str],
    worktree_path: Path | str,
    *,
    timeout: float | None = None,
    check: bool = False,
) -> GitResult:
    wt = Path(worktree_path)
    return run_git(list(args), cwd=wt, timeout=timeout, check=check)


def popen_git_worktree(args: Sequence[str], worktree_path: Path | str):
    return popen_git(list(args), cwd=Path(worktree_path))


def resolve_common_dir(worktree_path: Path | str) -> Path:
    r = run_git_worktree(["rev-parse", "--git-common-dir"], worktree_path, check=True)
    raw = r.stdout.strip()
    p = Path(raw)
    if not p.is_absolute():
        p = (Path(worktree_path) / p).resolve()
    else:
        p = p.resolve()
    return p


def assert_worktree_owns_bare(worktree_path: Path | str, bare_path: Path | str) -> None:
    common = resolve_common_dir(worktree_path)
    bare = Path(bare_path).resolve()
    if common != bare:
        raise ValidationError(
            "worktree does not belong to project bare repository",
            kind="enqueue_validation_failed",
            details={
                "worktree": str(worktree_path),
                "common_dir": str(common),
                "bare": str(bare),
            },
        )


def worktree_list_porcelain(bare_path: Path | str) -> list[dict[str, str]]:
    from orch.git.ref import run_git_ref

    r = run_git_ref(["worktree", "list", "--porcelain"], bare_path, check=True)
    entries: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in r.stdout.splitlines():
        if not line.strip():
            if current:
                entries.append(current)
                current = {}
            continue
        if line.startswith("worktree "):
            if current:
                entries.append(current)
            current = {"worktree": line[len("worktree ") :]}
        elif line.startswith("HEAD "):
            current["HEAD"] = line[len("HEAD ") :]
        elif line.startswith("branch "):
            current["branch"] = line[len("branch ") :]
        elif line == "bare":
            current["bare"] = "1"
        elif line == "detached":
            current["detached"] = "1"
        elif line == "locked":
            current["locked"] = "1"
        elif line.startswith("locked "):
            current["locked"] = "1"
            current["lock_reason"] = line[len("locked ") :]
    if current:
        entries.append(current)
    return entries


def assert_worktree_registered(worktree_path: Path | str, bare_path: Path | str) -> None:
    target = Path(worktree_path).resolve()
    entries = worktree_list_porcelain(bare_path)
    paths = [Path(e["worktree"]).resolve() for e in entries if "worktree" in e]
    if target not in paths:
        raise GitError(
            "worktree is not registered in bare repository (orphan)",
            details={"worktree": str(target), "bare": str(bare_path)},
        )
