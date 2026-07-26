"""Lifecycle hooks (V12-012) — argv allowlist, shell=False, timeout, output cap."""

from __future__ import annotations

import json
import subprocess
from typing import Any

from orch.errors import ValidationError

HOOK_ALLOWLIST = frozenset(
    {
        "WorktreeCreated",
        "AgentRegistered",
        "AgentStarting",
        "AgentIdle",
        "AgentCompleted",
        "AgentFailed",
        "TakeoverStarted",
        "TakeoverReleased",
        "BeforeWorktreeRemove",
        "WorktreeRemoved",
    }
)

# Disallow secondary shell launchers in argv[0]
_FORBIDDEN_ARGV0 = frozenset(
    {
        "cmd",
        "cmd.exe",
        "powershell",
        "powershell.exe",
        "pwsh",
        "pwsh.exe",
        "bash",
        "sh",
        "zsh",
        "fish",
    }
)

DEFAULT_TIMEOUT_SEC = 10.0
DEFAULT_MAX_OUTPUT_BYTES = 32_768


def validate_hook_argv(argv: list[str]) -> None:
    if not argv or not isinstance(argv, list):
        raise ValidationError("hook argv must be a non-empty list", kind="hook_invalid")
    if not all(isinstance(x, str) and x for x in argv):
        raise ValidationError("hook argv entries must be non-empty strings", kind="hook_invalid")
    head = argv[0].lower().rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
    # Allow powershell -File script.ps1 pattern only when -File present (not -Command)
    if head in _FORBIDDEN_ARGV0:
        joined = " ".join(argv).lower()
        if "-command" in joined or "/c" == argv[1].lower() if len(argv) > 1 else False:
            raise ValidationError(
                "secondary shell string execution is forbidden",
                kind="hook_shell_forbidden",
            )
        if head.startswith("powershell") or head == "pwsh":
            if "-file" not in [a.lower() for a in argv]:
                raise ValidationError(
                    "powershell hooks must use -File, not -Command",
                    kind="hook_shell_forbidden",
                )
        if head in {"cmd", "cmd.exe", "bash", "sh", "zsh", "fish"}:
            raise ValidationError(
                f"argv0 {argv[0]!r} is not allowed",
                kind="hook_shell_forbidden",
            )


def run_hook(
    name: str,
    *,
    argv: list[str],
    payload: dict[str, Any],
    timeout_seconds: float = DEFAULT_TIMEOUT_SEC,
    blocking: bool = False,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> dict[str, Any]:
    if name not in HOOK_ALLOWLIST:
        raise ValidationError(
            f"hook {name!r} not in allowlist",
            kind="hook_not_allowed",
            details={"allowed": sorted(HOOK_ALLOWLIST)},
        )
    validate_hook_argv(argv)

    # Never put secrets into payload
    safe = dict(payload)
    for k in list(safe.keys()):
        lk = k.lower()
        if any(s in lk for s in ("password", "token", "secret", "authorization")):
            safe[k] = "***"

    stdin_data = json.dumps(
        {"hook": name, "payload": safe}, ensure_ascii=False
    ).encode("utf-8")

    try:
        completed = subprocess.run(  # noqa: S603
            argv,
            input=stdin_data,
            capture_output=True,
            timeout=timeout_seconds,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        result = {
            "hook": name,
            "ok": False,
            "blocking": blocking,
            "error": "timeout",
            "timeout_seconds": timeout_seconds,
        }
        if blocking:
            raise ValidationError(
                f"blocking hook {name} timed out",
                kind="hook_blocking_failed",
                details=result,
            ) from exc
        return result
    except OSError as exc:
        result = {
            "hook": name,
            "ok": False,
            "blocking": blocking,
            "error": str(exc),
        }
        if blocking:
            raise ValidationError(
                f"blocking hook {name} failed to start",
                kind="hook_blocking_failed",
                details=result,
            ) from exc
        return result

    out = (completed.stdout or b"")[:max_output_bytes]
    err = (completed.stderr or b"")[:max_output_bytes]
    ok = completed.returncode == 0
    result = {
        "hook": name,
        "ok": ok,
        "blocking": blocking,
        "exit_code": completed.returncode,
        "stdout_len": len(completed.stdout or b""),
        "stderr_len": len(completed.stderr or b""),
        "stdout_preview": out.decode("utf-8", errors="replace")[:500],
        "stderr_preview": err.decode("utf-8", errors="replace")[:500],
    }
    if blocking and not ok:
        raise ValidationError(
            f"blocking hook {name} failed",
            kind="hook_blocking_failed",
            details=result,
        )
    return result


def load_hooks_config(config: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not config:
        return {}
    runtime = config.get("runtime") or {}
    hooks = runtime.get("hooks") or {}
    if not isinstance(hooks, dict):
        return {}
    return hooks
