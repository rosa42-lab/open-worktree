"""Audit log writer (task T-0107)."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from orch.errors import DbError
from orch.util import utc_now_iso

AUDIT_ACTIONS = frozenset(
    {
        "enqueued",
        "merge_claimed",
        "merge_started",
        "merge_succeeded",
        "merge_aborted_conflict",
        "merge_aborted_precheck",
        "merge_aborted_recovery_required",
        "retried",
        "skipped",
        "reset_stuck",
        "cleanup_pruned",
        "project_locked",
        "project_unlocked",
        "config_updated",
        "invalid_transition",
    }
)


def write_audit(
    conn: sqlite3.Connection,
    action: str,
    *,
    task_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    if action not in AUDIT_ACTIONS:
        raise DbError(
            f"unknown audit action: {action}",
            details={"action": action, "allowed": sorted(AUDIT_ACTIONS)},
        )
    detail_json = json.dumps(detail or {}, ensure_ascii=False)
    try:
        conn.execute(
            "INSERT INTO audit_log(task_id, action, detail, created_at) VALUES (?, ?, ?, ?)",
            (task_id, action, detail_json, utc_now_iso()),
        )
    except sqlite3.Error as exc:
        raise DbError(f"audit write failed: {exc}", details={"error": str(exc)}) from exc
