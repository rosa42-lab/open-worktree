"""develop_publish / master_release 状态机（设计 §9.4 / §10）。"""

from __future__ import annotations

from orch.errors import ValidationError
from orch.promotion.repo import PROMOTION_STATES

# from_state -> frozenset(to_state)
DEVELOP_PUBLISH_TRANSITIONS: dict[str, frozenset[str]] = {
    "created": frozenset({"prechecking", "cancelled"}),
    "prechecking": frozenset(
        {
            "ready",
            "failed_safe_to_retry",
            "blocked",
            "manual_required",
            "cancelled",
        }
    ),
    "ready": frozenset({"executing", "cancelled", "reconciling"}),
    "executing": frozenset(
        {
            "succeeded",
            "awaiting_checks",
            "reconciling",
            "failed_safe_to_retry",
            "manual_required",
            "blocked",
        }
    ),
    "awaiting_checks": frozenset({"ready_to_merge", "blocked", "manual_required"}),
    "ready_to_merge": frozenset({"published_pending_sync", "blocked", "manual_required"}),
    "published_pending_sync": frozenset({"succeeded", "reconciling", "manual_required"}),
    "reconciling": frozenset(
        {"succeeded", "failed_safe_to_retry", "manual_required", "cancelled"}
    ),
    "failed_safe_to_retry": frozenset({"prechecking", "cancelled", "reconciling"}),
    "blocked": frozenset({"reconciling", "cancelled"}),
    "manual_required": frozenset({"cancelled", "reconciling"}),
    "succeeded": frozenset(),
    "released": frozenset(),
    "cancelled": frozenset(),
}

# 设计 §10 master_release
MASTER_RELEASE_TRANSITIONS: dict[str, frozenset[str]] = {
    "created": frozenset({"prechecking", "cancelled"}),
    "prechecking": frozenset(
        {"awaiting_checks", "blocked", "manual_required", "cancelled"}
    ),
    "awaiting_checks": frozenset(
        {"awaiting_approval", "ready_to_merge", "blocked", "manual_required"}
    ),
    "awaiting_approval": frozenset({"ready_to_merge", "blocked", "manual_required"}),
    "ready_to_merge": frozenset(
        {"master_merged_pending_sync", "blocked", "manual_required"}
    ),
    "master_merged_pending_sync": frozenset(
        {"syncing", "blocked", "reconciling", "manual_required"}
    ),
    "syncing": frozenset({"released", "reconciling", "manual_required", "blocked"}),
    "reconciling": frozenset(
        {
            "awaiting_checks",
            "syncing",
            "master_merged_pending_sync",
            "manual_required",
            "blocked",
            "cancelled",
        }
    ),
    "blocked": frozenset({"reconciling", "cancelled"}),
    "manual_required": frozenset({"cancelled", "reconciling"}),
    "released": frozenset(),
    "cancelled": frozenset(),
    # unused on master path but present in PROMOTION_STATES
    "ready": frozenset(),
    "executing": frozenset(),
    "published_pending_sync": frozenset(),
    "succeeded": frozenset(),
    "failed_safe_to_retry": frozenset({"prechecking", "cancelled", "reconciling"}),
}

MASTER_CANCELABLE = frozenset({"created", "blocked", "manual_required"})


def assert_develop_transition(from_state: str, to_state: str) -> None:
    if from_state not in PROMOTION_STATES or to_state not in PROMOTION_STATES:
        raise ValidationError(
            f"unknown promotion state {from_state!r} -> {to_state!r}",
            kind="promotion_invalid_state",
            details={"from": from_state, "to": to_state},
        )
    allowed = DEVELOP_PUBLISH_TRANSITIONS.get(from_state, frozenset())
    if to_state not in allowed:
        raise ValidationError(
            f"illegal develop_publish transition {from_state!r} -> {to_state!r}",
            kind="promotion_illegal_transition",
            details={"from": from_state, "to": to_state},
        )


def assert_master_transition(from_state: str, to_state: str) -> None:
    if from_state not in PROMOTION_STATES or to_state not in PROMOTION_STATES:
        raise ValidationError(
            f"unknown promotion state {from_state!r} -> {to_state!r}",
            kind="promotion_invalid_state",
            details={"from": from_state, "to": to_state},
        )
    allowed = MASTER_RELEASE_TRANSITIONS.get(from_state, frozenset())
    if to_state not in allowed:
        raise ValidationError(
            f"illegal master_release transition {from_state!r} -> {to_state!r}",
            kind="promotion_illegal_transition",
            details={"from": from_state, "to": to_state, "kind": "master_release"},
        )
