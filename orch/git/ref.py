"""Bare-repo / ref-only git commands via --git-dir."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from orch.git._runner import GitResult, run_git


def run_git_ref(
    args: Sequence[str],
    bare_path: Path | str,
    *,
    timeout: float | None = None,
    check: bool = False,
) -> GitResult:
    bare = Path(bare_path)
    full = ["--git-dir", str(bare), *list(args)]
    return run_git(full, timeout=timeout, check=check)


def run_git_global(
    args: Sequence[str],
    *,
    timeout: float | None = None,
    check: bool = False,
) -> GitResult:
    """Commands that must not fake --git-dir (e.g. check-ref-format)."""
    return run_git(list(args), timeout=timeout, check=check)
