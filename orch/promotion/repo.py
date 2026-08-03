"""promotion_runs / events / tasks 持久化（V13-006 / 设计 §11.1–§11.3）。"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from typing import Any

from orch.errors import DbError, ValidationError
from orch.util import utc_now_iso

PROMOTION_KINDS = frozenset({"develop_publish", "master_release"})
PROMOTION_MODES = frozenset({"direct_ff", "candidate_pr", "promotion_pr"})
PROMOTION_STATES = frozenset(
    {
        "created",
        "prechecking",
        "ready",
        "executing",
        "awaiting_checks",
        "awaiting_approval",
        "ready_to_merge",
        "published_pending_sync",
        "master_merged_pending_sync",
        "syncing",
        "succeeded",
        "released",
        "blocked",
        "reconciling",
        "failed_safe_to_retry",
        "manual_required",
        "cancelled",
    }
)
TERMINAL_FOR_ACTIVE = frozenset({"succeeded", "released", "cancelled"})

_SECRET_KEY_RE = re.compile(
    r"(token|password|secret|authorization|credential|api[_-]?key|private[_-]?key)",
    re.IGNORECASE,
)


def new_promotion_id() -> str:
    return "promo_" + uuid.uuid4().hex


def redact_event_detail(detail: Any) -> str | None:
    """事件 detail 只保留脱敏摘要；禁止 secret 键与长敏感串。"""
    if detail is None:
        return None
    if isinstance(detail, str):
        text = detail
        if _SECRET_KEY_RE.search(text):
            return json.dumps({"redacted": True, "reason": "secret_pattern"}, ensure_ascii=False)
        return text[:800]
    if not isinstance(detail, dict):
        return json.dumps({"value": str(detail)[:400]}, ensure_ascii=False)
    clean: dict[str, Any] = {}
    for key, value in detail.items():
        if _SECRET_KEY_RE.search(str(key)):
            clean[str(key)] = "<redacted>"
            continue
        if isinstance(value, str):
            if _SECRET_KEY_RE.search(value):
                clean[str(key)] = "<redacted>"
            else:
                clean[str(key)] = value[:400]
        elif isinstance(value, (int, float, bool)) or value is None:
            clean[str(key)] = value
        else:
            clean[str(key)] = str(value)[:400]
    return json.dumps(clean, ensure_ascii=False)


def _row_to_run(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def create_run(
    conn: sqlite3.Connection,
    *,
    project_name: str,
    kind: str,
    mode: str,
    remote_name: str,
    provider: str,
    source_ref: str,
    target_ref: str,
    source_sha: str,
    target_sha_before: str,
    created_by: str,
    state: str = "created",
    verification_record_id: str | None = None,
    promotion_id: str | None = None,
) -> dict[str, Any]:
    if kind not in PROMOTION_KINDS:
        raise ValidationError(
            f"invalid promotion kind: {kind}",
            kind="promotion_invalid_kind",
            details={"kind": kind},
        )
    if mode not in PROMOTION_MODES:
        raise ValidationError(
            f"invalid promotion mode: {mode}",
            kind="promotion_invalid_mode",
            details={"mode": mode},
        )
    if state not in PROMOTION_STATES:
        raise ValidationError(
            f"invalid promotion state: {state}",
            kind="promotion_invalid_state",
            details={"state": state},
        )
    pid = promotion_id or new_promotion_id()
    now = utc_now_iso()
    try:
        conn.execute(
            """
            INSERT INTO promotion_runs (
              id, project_name, kind, mode, state,
              remote_name, provider, source_ref, target_ref,
              source_sha, target_sha_before, published_sha, observed_target_sha,
              verification_record_id, post_verification_record_id,
              external_id, external_url, created_by, created_at, updated_at,
              finished_at, last_error
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                pid,
                project_name,
                kind,
                mode,
                state,
                remote_name,
                provider,
                source_ref,
                target_ref,
                source_sha,
                target_sha_before,
                None,
                None,
                verification_record_id,
                None,
                None,
                None,
                created_by,
                now,
                now,
                None,
                None,
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise ValidationError(
            "promotion uniqueness violated (active kind or source_sha)",
            kind="promotion_conflict",
            details={"error": str(exc)[:200], "project": project_name, "kind": kind},
        ) from exc
    except sqlite3.Error as exc:
        raise DbError(f"create promotion_run failed: {exc}", details={"error": str(exc)}) from exc
    run = get_run(conn, pid)
    assert run is not None
    return run


def get_run(conn: sqlite3.Connection, promotion_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM promotion_runs WHERE id = ?",
        (promotion_id,),
    ).fetchone()
    return _row_to_run(row) if row is not None else None


def find_active(
    conn: sqlite3.Connection,
    project_name: str,
    kind: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT * FROM promotion_runs
        WHERE project_name = ? AND kind = ?
          AND state NOT IN ('succeeded','released','cancelled')
        LIMIT 1
        """,
        (project_name, kind),
    ).fetchone()
    return _row_to_run(row) if row is not None else None


def update_run_fields(
    conn: sqlite3.Connection,
    promotion_id: str,
    **fields: Any,
) -> dict[str, Any]:
    allowed = {
        "state",
        "published_sha",
        "observed_target_sha",
        "verification_record_id",
        "post_verification_record_id",
        "external_id",
        "external_url",
        "finished_at",
        "last_error",
    }
    unknown = set(fields) - allowed
    if unknown:
        raise ValidationError(
            f"unknown promotion update fields: {sorted(unknown)}",
            kind="promotion_invalid_update",
            details={"unknown": sorted(unknown)},
        )
    if "state" in fields and fields["state"] not in PROMOTION_STATES:
        raise ValidationError(
            f"invalid promotion state: {fields['state']}",
            kind="promotion_invalid_state",
            details={"state": fields["state"]},
        )
    fields = dict(fields)
    fields["updated_at"] = utc_now_iso()
    cols = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [promotion_id]
    try:
        cur = conn.execute(
            f"UPDATE promotion_runs SET {cols} WHERE id = ?",
            values,
        )
    except sqlite3.IntegrityError as exc:
        raise ValidationError(
            "promotion uniqueness violated on update",
            kind="promotion_conflict",
            details={"error": str(exc)[:200]},
        ) from exc
    except sqlite3.Error as exc:
        raise DbError(f"update promotion_run failed: {exc}", details={"error": str(exc)}) from exc
    if cur.rowcount != 1:
        raise ValidationError(
            f"promotion not found: {promotion_id}",
            kind="promotion_not_found",
            details={"id": promotion_id},
        )
    run = get_run(conn, promotion_id)
    assert run is not None
    return run


def next_event_seq(conn: sqlite3.Connection, promotion_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) FROM promotion_events WHERE promotion_id = ?",
        (promotion_id,),
    ).fetchone()
    return int(row[0]) + 1


def append_event(
    conn: sqlite3.Connection,
    *,
    promotion_id: str,
    event_type: str,
    source: str,
    detail: Any = None,
) -> dict[str, Any]:
    seq = next_event_seq(conn, promotion_id)
    now = utc_now_iso()
    detail_text = redact_event_detail(detail)
    try:
        conn.execute(
            """
            INSERT INTO promotion_events (
              promotion_id, seq, event_type, source, detail, created_at
            ) VALUES (?,?,?,?,?,?)
            """,
            (promotion_id, seq, event_type, source, detail_text, now),
        )
    except sqlite3.Error as exc:
        raise DbError(f"append promotion_event failed: {exc}", details={"error": str(exc)}) from exc
    return {
        "promotion_id": promotion_id,
        "seq": seq,
        "event_type": event_type,
        "source": source,
        "detail": detail_text,
        "created_at": now,
    }


def list_events(conn: sqlite3.Connection, promotion_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT promotion_id, seq, event_type, source, detail, created_at
        FROM promotion_events
        WHERE promotion_id = ?
        ORDER BY seq ASC
        """,
        (promotion_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def link_tasks(
    conn: sqlite3.Connection,
    promotion_id: str,
    links: list[tuple[str, str]],
) -> None:
    """links: [(task_id, merged_commit), ...] — 不用于 release-sync。"""
    try:
        for task_id, merged_commit in links:
            conn.execute(
                """
                INSERT OR IGNORE INTO promotion_tasks (
                  promotion_id, task_id, merged_commit
                ) VALUES (?,?,?)
                """,
                (promotion_id, task_id, merged_commit),
            )
    except sqlite3.Error as exc:
        raise DbError(f"link promotion_tasks failed: {exc}", details={"error": str(exc)}) from exc


def list_tasks(conn: sqlite3.Connection, promotion_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT promotion_id, task_id, merged_commit
        FROM promotion_tasks
        WHERE promotion_id = ?
        ORDER BY task_id
        """,
        (promotion_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_runs(
    conn: sqlite3.Connection,
    project_name: str,
    *,
    kind: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    if kind:
        rows = conn.execute(
            """
            SELECT * FROM promotion_runs
            WHERE project_name = ? AND kind = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (project_name, kind, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM promotion_runs
            WHERE project_name = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (project_name, limit),
        ).fetchall()
    return [_row_to_run(r) for r in rows]
