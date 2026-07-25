"""Branch name checks via git check-ref-format."""

from __future__ import annotations

from orch.errors import UsageError, ValidationError
from orch.git.ref import run_git_global


def check_ref_format_branch(branch: str) -> str:
    if not branch:
        raise UsageError("branch name is empty")
    if "\x00" in branch:
        raise UsageError("branch name contains null byte")
    r = run_git_global(["check-ref-format", "--branch", branch])
    if not r.ok:
        raise ValidationError(
            f"invalid branch name: {branch!r}",
            kind="enqueue_validation_failed",
            details={"branch": branch, "stderr": r.stderr.strip()},
        )
    return branch
