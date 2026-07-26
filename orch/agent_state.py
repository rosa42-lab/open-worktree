"""Agent run lifecycle state machine (V12-003). Separate from tasks.status."""

from __future__ import annotations

from orch.errors import ExitCode, OrchError

# Closed sets — must match DB CHECK constraints in migrations.SCHEMA_V2_ADDITIVE_SQL
LIFECYCLE_STATES = frozenset(
    {
        "registered",
        "starting",
        "running",
        "pausing",
        "human_controlled",
        "resuming",
        "stopping",
        "exited",
        "lost",
        "reconciling",
        "manual_required",
        "archived",
    }
)

DESIRED_STATES = frozenset({"running", "paused", "stopped"})

OBSERVED_STATES = frozenset(
    {
        "starting",
        "running",
        "idle",
        "busy",
        "stopping",
        "exited",
        "unreachable",
    }
)

CONTROLLERS = frozenset({"agent", "human", "none"})

TERMINAL_LIFECYCLE = frozenset({"exited", "archived"})

CLEANUP_BLOCKING_LIFECYCLE = frozenset(
    {
        "starting",
        "running",
        "pausing",
        "human_controlled",
        "resuming",
        "stopping",
        "lost",
        "reconciling",
        "manual_required",
    }
)

# Table-driven transitions (from, to). from=None means create via register.
ALLOWED_LIFECYCLE: frozenset[tuple[str | None, str]] = frozenset(
    {
        (None, "registered"),
        ("registered", "starting"),
        ("registered", "archived"),
        ("starting", "running"),
        ("starting", "stopping"),
        ("starting", "lost"),
        ("running", "pausing"),
        ("running", "stopping"),
        ("running", "lost"),
        ("pausing", "human_controlled"),
        ("pausing", "lost"),
        ("pausing", "stopping"),
        ("human_controlled", "resuming"),
        ("human_controlled", "stopping"),
        ("resuming", "running"),
        ("resuming", "lost"),
        ("stopping", "exited"),
        ("stopping", "lost"),
        ("lost", "reconciling"),
        ("reconciling", "running"),
        ("reconciling", "human_controlled"),
        ("reconciling", "exited"),
        ("reconciling", "manual_required"),
        ("manual_required", "reconciling"),
        ("manual_required", "archived"),
        ("exited", "archived"),
    }
)


class InvalidAgentTransitionError(OrchError):
    def __init__(self, from_state: str | None, to_state: str) -> None:
        super().__init__(
            f"invalid agent lifecycle transition {from_state!r} -> {to_state!r}",
            code=ExitCode.GENERAL,
            kind="invalid_agent_transition",
            details={"from": from_state, "to": to_state},
        )


class InvalidAgentFieldError(OrchError):
    def __init__(self, field: str, value: str) -> None:
        super().__init__(
            f"invalid agent {field}: {value!r}",
            code=ExitCode.VALIDATION,
            kind="invalid_agent_field",
            details={"field": field, "value": value},
        )


def assert_lifecycle_transition(from_state: str | None, to_state: str) -> None:
    if to_state not in LIFECYCLE_STATES:
        raise InvalidAgentTransitionError(from_state, to_state)
    if (from_state, to_state) not in ALLOWED_LIFECYCLE:
        raise InvalidAgentTransitionError(from_state, to_state)


def assert_desired(value: str) -> None:
    if value not in DESIRED_STATES:
        raise InvalidAgentFieldError("desired_state", value)


def assert_observed(value: str) -> None:
    if value not in OBSERVED_STATES:
        raise InvalidAgentFieldError("observed_state", value)


def assert_controller(value: str) -> None:
    if value not in CONTROLLERS:
        raise InvalidAgentFieldError("controller", value)


def blocks_cleanup(state: str) -> bool:
    return state in CLEANUP_BLOCKING_LIFECYCLE
