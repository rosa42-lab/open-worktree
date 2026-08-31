"""Per-operation runtime capability gate (V19). Unknown operation = fail-closed."""

from __future__ import annotations

from orch.errors import ValidationError
from orch.runtime.adapter import CapabilityMatrix

# Caps required for this operation. Empty tuple = no capability prerequisite.
OPERATION_CAPS: dict[str, tuple[str, ...]] = {
    "observe": (),
    "health": ("global_health",),
    "agent_start": ("global_health",),
    "agent_start_prompt": ("global_health", "prompt_async"),
    "agent_stop_local": (),
    "create_session": ("create_session",),
    "get_session": ("get_session",),
    "fork": ("session_fork_api",),
    "attach_fork": ("attach_cli_fork",),
    "abort": ("abort",),
}


def assert_runtime_gate(
    operation: str,
    matrix: CapabilityMatrix,
) -> CapabilityMatrix:
    needed = OPERATION_CAPS.get(operation)
    if needed is None:
        raise ValidationError(
            f"unknown runtime operation: {operation}",
            kind="runtime_capability_unknown",
            details={"operation": operation},
        )
    for name in needed:
        if not getattr(matrix, name):
            raise ValidationError(
                f"runtime capability missing for {operation}: {name}",
                kind="runtime_capability_missing",
                details={"operation": operation, "capability": name},
            )
    return matrix
