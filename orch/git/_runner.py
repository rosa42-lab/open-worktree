"""Low-level git process runner."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from orch.constants import GIT_TIMEOUT_SEC
from orch.errors import GitError


@dataclass
class GitResult:
    returncode: int
    stdout: str
    stderr: str
    args: list[str]
    proc: subprocess.Popen[str] | None = None

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run_git(
    args: Sequence[str],
    *,
    cwd: Path | str | None = None,
    timeout: float | None = None,
    check: bool = False,
) -> GitResult:
    if not isinstance(args, (list, tuple)):
        raise TypeError("git args must be a list")
    cmd = ["git", *list(args)]
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd is not None else None,
            shell=False,
            check=False,
            text=True,
            capture_output=True,
            timeout=timeout if timeout is not None else GIT_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitError(
            f"git timed out: {' '.join(cmd)}",
            details={"args": cmd, "timeout": timeout or GIT_TIMEOUT_SEC},
        ) from exc
    except FileNotFoundError as exc:
        raise GitError("git executable not found", details={"args": cmd}) from exc

    result = GitResult(
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
        args=cmd,
    )
    if check and not result.ok:
        raise GitError(
            f"git failed ({result.returncode}): {result.stderr.strip() or result.stdout.strip()}",
            details={
                "args": cmd,
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
        )
    return result


def popen_git(
    args: Sequence[str],
    *,
    cwd: Path | str | None = None,
) -> subprocess.Popen[str]:
    """Start git and return Popen for interruptible merge (T-0408)."""
    cmd = ["git", *list(args)]
    return subprocess.Popen(
        cmd,
        cwd=str(cwd) if cwd is not None else None,
        shell=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
