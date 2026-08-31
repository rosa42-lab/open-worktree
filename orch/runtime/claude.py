"""Class B Claude headless slices: spawn, wait, record resume id.

Does not talk to OpenCode over HTTP. Does not keep a perpetual CLI shell.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

from orch.errors import ValidationError
from orch.runtime.binary import resolve_executor, spawn_env

_PY_HOST_KEYS = (
    "SYSTEMDRIVE",
    "SystemDrive",
    "PYTHONUTF8",
    "PYTHONIOENCODING",
    "PYTHONHOME",
)


@dataclass(frozen=True)
class ClaudeSliceResult:
    session_id: str
    exit_code: int
    argv: tuple[str, ...]
    running: bool = False


def _command_prefix(binary: Path) -> list[str]:
    if binary.suffix.lower() == ".py":
        return [sys.executable, str(binary)]
    return [str(binary)]


def build_slice_argv(
    *,
    binary: Path,
    prompt: str,
    session_id: str | None = None,
) -> tuple[list[str], str]:
    """First slice pins --session-id; continue uses --resume <id> (no new id)."""
    existing = str(session_id or "").strip()
    sid = existing or str(uuid.uuid4())
    flags: list[str] = ["-p", "--output-format", "text"]
    if existing:
        flags.extend(["--resume", sid])
    else:
        flags.extend(["--session-id", sid])
    flags.extend(["--", prompt])
    return [*_command_prefix(binary), *flags], sid


def run_slice(
    *,
    kind: str = "claude",
    binary: str | Path,
    prompt: str,
    session_id: str | None = None,
    cwd: str | Path | None = None,
    timeout_sec: float | None = 120.0,
) -> ClaudeSliceResult:
    """Run one Class B slice and wait for exit. Never marks running."""
    kind_key = str(kind or "").strip().lower()
    if kind_key != "claude":
        raise ValidationError(
            f"claude slice runner does not accept kind={kind}",
            kind="runtime_unknown_kind",
            details={"kind": kind},
        )
    text = str(prompt or "")
    if not text.strip():
        raise ValidationError(
            "Class B slice requires a prompt",
            kind="runtime_slice_prompt_required",
        )
    resolved = resolve_executor(kind="claude", path=str(binary))
    argv, sid = build_slice_argv(
        binary=resolved, prompt=text, session_id=session_id
    )
    env = spawn_env(binary=resolved)
    if resolved.suffix.lower() == ".py":
        for key in _PY_HOST_KEYS:
            val = os.environ.get(key)
            if val:
                env[key] = val
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd) if cwd is not None else None,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except OSError as exc:
        raise ValidationError(
            f"failed to spawn Class B executor: {exc}",
            kind="runtime_slice_failed",
            details={"error": str(exc)},
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ValidationError(
            "Class B slice timed out",
            kind="runtime_slice_failed",
            details={"timeout_sec": timeout_sec},
        ) from exc
    if proc.returncode != 0:
        raise ValidationError(
            f"Class B slice exited {proc.returncode}",
            kind="runtime_slice_failed",
            details={"exit_code": proc.returncode},
        )
    return ClaudeSliceResult(
        session_id=sid,
        exit_code=0,
        argv=tuple(argv),
        running=False,
    )
