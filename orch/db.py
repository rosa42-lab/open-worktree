"""SQLite connection management and schema bootstrap."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from orch.constants import DB_BUSY_TIMEOUT_MS, project_data_dir, project_db_path
from orch.errors import DbError
from orch.migrations import SCHEMA_V1_SQL, ensure_schema

# Back-compat alias: historical callers / tests expect SCHEMA_SQL == v1 DDL.
SCHEMA_SQL = SCHEMA_V1_SQL


def connect(db_path: Path | str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = sqlite3.connect(str(path), timeout=DB_BUSY_TIMEOUT_MS / 1000.0)
    except sqlite3.Error as exc:
        raise DbError(f"cannot open database: {path}", details={"error": str(exc)}) from exc
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute(f"PRAGMA busy_timeout = {DB_BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA temp_store = MEMORY")
    except sqlite3.Error as exc:
        conn.close()
        raise DbError(f"pragma failed: {exc}", details={"error": str(exc)}) from exc
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """
    Ensure project DB is at schema 2 (idempotent).

    Empty DB -> full schema 2; exact v1.1 shape -> additive migration;
    already v2 -> no-op. Ambiguous / unsupported schemas raise.
    """
    try:
        ensure_schema(conn)
    except DbError:
        raise
    except Exception as exc:  # noqa: BLE001
        # OrchError subclasses already carry exit codes; re-raise as-is.
        from orch.errors import OrchError

        if isinstance(exc, OrchError):
            raise
        raise DbError(f"schema init failed: {exc}", details={"error": str(exc)}) from exc


def open_project_db(project: str, *, init: bool = False) -> sqlite3.Connection:
    project_data_dir(project).mkdir(parents=True, exist_ok=True)
    conn = connect(project_db_path(project))
    if init:
        init_schema(conn)
    return conn


@contextmanager
def immediate_transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE short write transaction. Never run Git inside."""
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.Error as exc:
        raise DbError(f"BEGIN IMMEDIATE failed: {exc}", details={"error": str(exc)}) from exc
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        raise
