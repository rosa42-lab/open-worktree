"""Host-level runtime commands (v1.2)."""

from __future__ import annotations

from typing import Any

from orch.errors import UsageError
from orch.runtime.probe import run_capability_probe
from orch.runtime.service import runtime_start, runtime_status, runtime_stop


def cmd_runtime_probe(
    *,
    base_url: str | None = None,
    port: int | None = None,
    password: str | None = None,
    username: str | None = None,
    keep_server: bool = False,
) -> dict[str, Any]:
    """
    Probe OpenCode Server capabilities.

    Does not take project lock, write project DB, or mutate orch-managed Git.
    """
    return run_capability_probe(
        base_url=base_url,
        port=port,
        password=password,
        username=username,
        keep_server=keep_server,
    )


def cmd_runtime_start(
    *,
    port: int | None = None,
    password: str | None = None,
    username: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Start or reuse host OpenCode Server; register in ~/.orchestrator/runtime/."""
    return runtime_start(
        port=port,
        password=password,
        username=username,
        external_base_url=base_url,
    )


def cmd_runtime_status() -> dict[str, Any]:
    return runtime_status()


def cmd_runtime_stop(*, force: bool = False) -> dict[str, Any]:
    return runtime_stop(force=force)


def require_runtime_cmd(name: str) -> None:
    if name not in {"probe", "start", "status", "stop"}:
        raise UsageError(f"unknown runtime command: {name}")
