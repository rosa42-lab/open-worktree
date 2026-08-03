"""verification_records 持久化。"""

from __future__ import annotations

import json
import sqlite3
from typing import Any


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["commands"] = json.loads(d.pop("commands_json"))
    d["results"] = json.loads(d.pop("results_json"))
    return d


def insert_record(conn: sqlite3.Connection, record: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO verification_records (
          id, project_name, scope, commit_sha, status,
          commands_json, results_json, created_by,
          started_at, finished_at, expires_at, topic_id, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            record["id"],
            record["project_name"],
            record["scope"],
            record["commit_sha"],
            record["status"],
            json.dumps(record["commands"], ensure_ascii=False),
            json.dumps(record["results"], ensure_ascii=False),
            record["created_by"],
            record["started_at"],
            record.get("finished_at"),
            record.get("expires_at"),
            record.get("topic_id"),
            record["created_at"],
        ),
    )


def get_by_id(conn: sqlite3.Connection, record_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM verification_records WHERE id = ?",
        (record_id,),
    ).fetchone()
    return row_to_dict(row) if row is not None else None


def list_by_commit(
    conn: sqlite3.Connection,
    project: str,
    commit_sha: str,
    *,
    scope: str | None = None,
) -> list[dict[str, Any]]:
    if scope:
        rows = conn.execute(
            """
            SELECT * FROM verification_records
            WHERE project_name = ? AND commit_sha = ? AND scope = ?
            ORDER BY created_at DESC
            """,
            (project, commit_sha, scope),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM verification_records
            WHERE project_name = ? AND commit_sha = ?
            ORDER BY created_at DESC
            """,
            (project, commit_sha),
        ).fetchall()
    return [row_to_dict(r) for r in rows]


def list_by_topic(
    conn: sqlite3.Connection,
    project: str,
    topic_id: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM verification_records
        WHERE project_name = ? AND topic_id = ?
        ORDER BY created_at DESC
        """,
        (project, topic_id),
    ).fetchall()
    return [row_to_dict(r) for r in rows]


def update_status(
    conn: sqlite3.Connection,
    record_id: str,
    status: str,
    *,
    finished_at: str | None = None,
) -> None:
    if finished_at is not None:
        conn.execute(
            """
            UPDATE verification_records
            SET status = ?, finished_at = ?
            WHERE id = ?
            """,
            (status, finished_at, record_id),
        )
    else:
        conn.execute(
            "UPDATE verification_records SET status = ? WHERE id = ?",
            (status, record_id),
        )


def supersede_topic_records(
    conn: sqlite3.Connection,
    project: str,
    topic_id: str,
    *,
    except_id: str | None = None,
) -> int:
    if except_id:
        cur = conn.execute(
            """
            UPDATE verification_records
            SET status = 'superseded'
            WHERE project_name = ? AND topic_id = ?
              AND status IN ('running', 'passed')
              AND id != ?
            """,
            (project, topic_id, except_id),
        )
    else:
        cur = conn.execute(
            """
            UPDATE verification_records
            SET status = 'superseded'
            WHERE project_name = ? AND topic_id = ?
              AND status IN ('running', 'passed')
            """,
            (project, topic_id),
        )
    return int(cur.rowcount)
