"""双冻结守卫（D5）：active master_release 期间拒 promote-develop execute 与 local merge。"""

from __future__ import annotations

import sqlite3
from typing import Any

from orch.errors import PrecheckError, ValidationError
from orch.promotion.repo import find_active


class ReleaseFreezeError(ValidationError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message,
            kind="release_freeze",
            details=details or {},
        )


def active_master_release(conn: sqlite3.Connection, project: str) -> dict[str, Any] | None:
    return find_active(conn, project, "master_release")


def assert_no_release_freeze(
    conn: sqlite3.Connection,
    project: str,
    *,
    action: str,
) -> None:
    """action: promote_develop | merge_claim"""
    active = active_master_release(conn, project)
    if active is None:
        return
    raise ReleaseFreezeError(
        f"active master_release {active['id']} blocks {action} "
        f"(state={active['state']}); finish release-sync or cancel",
        details={
            "promotion_id": active["id"],
            "state": active["state"],
            "action": action,
        },
    )


def assert_no_release_freeze_precheck(
    conn: sqlite3.Connection,
    project: str,
) -> None:
    """供 merge claim：抛 PrecheckError 以匹配 merge 错误模型。"""
    active = active_master_release(conn, project)
    if active is None:
        return
    raise PrecheckError(
        f"release_freeze: active master_release {active['id']} "
        f"(state={active['state']}); merge into local develop blocked",
        details={
            "kind": "release_freeze",
            "promotion_id": active["id"],
            "state": active["state"],
        },
    )
