"""Naming and path validation (task T-0103)."""

from __future__ import annotations

import re
from pathlib import Path

from orch.errors import UsageError

NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")
_UNSAFE_BRANCH_CHARS = re.compile(r"[\x00-\x1f\s@{}\\]")


def validate_project_name(name: str) -> str:
    if not name or not NAME_RE.match(name):
        raise UsageError(
            f"invalid project name: {name!r}",
            details={"name": name, "pattern": NAME_RE.pattern},
        )
    return name


def validate_agent_name(name: str) -> str:
    if not name or not NAME_RE.match(name):
        raise UsageError(
            f"invalid agent name: {name!r}",
            details={"name": name, "pattern": NAME_RE.pattern},
        )
    return name


def _reject_control_or_null(s: str, label: str) -> None:
    if "\x00" in s:
        raise UsageError(f"{label} contains null byte", details={label: "null"})
    for ch in s:
        if ord(ch) < 32 and ch not in ("\t",):  # paths shouldn't have control chars
            raise UsageError(
                f"{label} contains control character",
                details={label: repr(s)},
            )


def normalize_path(path_str: str, *, label: str = "path") -> Path:
    if path_str is None or path_str == "":
        raise UsageError(f"{label} is required")
    _reject_control_or_null(path_str, label)
    # Reject explicit null-like and empty segments after expand — resolve later.
    p = Path(path_str).expanduser()
    try:
        resolved = p.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise UsageError(f"invalid {label}: {path_str}", details={"error": str(exc)}) from exc
    # Disallow path strings that clearly try to escape via null/control already done.
    return resolved


def branch_safe_name(branch: str) -> str:
    """Convert branch name to worktree directory safe segment."""
    if not branch:
        raise UsageError("branch name is empty")
    _reject_control_or_null(branch, "branch")
    if ".." in branch or branch.startswith("/") or "\\0" in branch:
        raise UsageError(f"unsafe branch name: {branch!r}")
    safe = branch.replace("/", "__")
    safe = _UNSAFE_BRANCH_CHARS.sub("_", safe)
    if not safe or safe in (".", ".."):
        raise UsageError(f"unsafe branch name after sanitize: {branch!r}")
    return safe
