"""Task status transitions (task T-0106)."""

from __future__ import annotations

from orch.errors import OrchError, ExitCode

STATUSES = frozenset(
    {
        "pending",
        "merging",
        "merged",
        "conflict",
        "recovery_required",
        "skipped",
    }
)

BLOCKING_STATUSES = frozenset({"conflict", "recovery_required"})
TERMINAL_STATUSES = frozenset({"merged", "skipped"})
ACTIVE_STATUSES = frozenset({"pending", "merging", "conflict", "recovery_required"})

# (from, to) — from None means create via enqueue
ALLOWED: frozenset[tuple[str | None, str]] = frozenset(
    {
        (None, "pending"),
        ("pending", "merging"),
        ("merging", "merged"),
        ("merging", "conflict"),
        ("merging", "recovery_required"),
        ("pending", "skipped"),
        ("conflict", "skipped"),
        ("conflict", "pending"),
        ("merging", "pending"),
        ("recovery_required", "pending"),
        ("recovery_required", "merged"),  # §7.11 evidence path
    }
)


class InvalidTransitionError(OrchError):
    def __init__(self, from_status: str | None, to_status: str) -> None:
        super().__init__(
            f"invalid transition {from_status!r} -> {to_status!r}",
            code=ExitCode.GENERAL,
            kind="invalid_transition",
            details={"from": from_status, "to": to_status},
        )


def assert_transition(from_status: str | None, to_status: str) -> None:
    if to_status not in STATUSES:
        raise InvalidTransitionError(from_status, to_status)
    if (from_status, to_status) not in ALLOWED:
        raise InvalidTransitionError(from_status, to_status)


def is_blocking(status: str) -> bool:
    return status in BLOCKING_STATUSES
