"""SQLite connection management and schema (task T-0105)."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from orch.constants import DB_BUSY_TIMEOUT_MS, project_data_dir, project_db_path
from orch.errors import DbError

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  agent_name TEXT NOT NULL,
  branch_name TEXT NOT NULL,
  worktree_path TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL,
  submitted_at TEXT NOT NULL,
  source_commit TEXT NOT NULL,
  target_head_before TEXT NOT NULL,
  target_commit_at_claim TEXT,
  queue_seq INTEGER NOT NULL UNIQUE,
  claimed_at TEXT,
  finished_at TEXT,
  merged_commit TEXT,
  last_error TEXT,
  conflict_files TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  archived_at TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT,
  action TEXT NOT NULL,
  detail TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS counters (
  name TEXT PRIMARY KEY,
  value INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_status_priority
  ON tasks(status, priority, submitted_at);

CREATE INDEX IF NOT EXISTS idx_tasks_status_seq
  ON tasks(status, queue_seq);

CREATE INDEX IF NOT EXISTS idx_tasks_branch
  ON tasks(branch_name);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_branch_active
  ON tasks(branch_name)
  WHERE status IN ('pending', 'merging', 'conflict', 'recovery_required');

INSERT OR IGNORE INTO counters(name, value) VALUES ('queue_seq', 0);
"""


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
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    except sqlite3.Error as exc:
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
