"""Schema migration framework (v1.1 -> schema 2). V12-002."""

from __future__ import annotations

import sqlite3
from typing import Any

from orch.errors import DbError, ExitCode, OrchError

SCHEMA_VERSION = 2

# ---------------------------------------------------------------------------
# v1.1 shape (user_version=0, exact tables/columns/indexes)
# ---------------------------------------------------------------------------

V1_TABLE_COLUMNS: dict[str, frozenset[str]] = {
    "tasks": frozenset(
        {
            "id",
            "agent_name",
            "branch_name",
            "worktree_path",
            "priority",
            "status",
            "submitted_at",
            "source_commit",
            "target_head_before",
            "target_commit_at_claim",
            "queue_seq",
            "claimed_at",
            "finished_at",
            "merged_commit",
            "last_error",
            "conflict_files",
            "attempts",
            "archived_at",
        }
    ),
    "audit_log": frozenset(
        {"id", "task_id", "action", "detail", "created_at"}
    ),
    "counters": frozenset({"name", "value"}),
}

V1_INDEXES = frozenset(
    {
        "idx_tasks_status_priority",
        "idx_tasks_status_seq",
        "idx_tasks_branch",
        "idx_tasks_branch_active",
    }
)

SCHEMA_V1_SQL = """
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

# ---------------------------------------------------------------------------
# schema 2 additive objects (also used for empty-DB full init)
# ---------------------------------------------------------------------------

SCHEMA_V2_ADDITIVE_SQL = """
CREATE TABLE IF NOT EXISTS agent_runs (
  id TEXT PRIMARY KEY,
  project_name TEXT NOT NULL,
  agent_name TEXT NOT NULL,
  branch_name TEXT NOT NULL,
  worktree_path TEXT NOT NULL,
  task_id TEXT,
  runtime_kind TEXT NOT NULL,
  runtime_server_id TEXT NOT NULL,
  session_id TEXT,
  state TEXT NOT NULL CHECK(state IN (
    'registered','starting','running','pausing','human_controlled',
    'resuming','stopping','exited','lost','reconciling',
    'manual_required','archived'
  )),
  desired_state TEXT NOT NULL CHECK(desired_state IN ('running','paused','stopped')),
  observed_state TEXT NOT NULL CHECK(observed_state IN (
    'starting','running','idle','busy','stopping','exited','unreachable'
  )),
  controller TEXT NOT NULL CHECK(controller IN ('agent','human','none')),
  controller_generation INTEGER NOT NULL DEFAULT 0,
  worker_pid INTEGER,
  worker_hostname TEXT,
  worker_nonce TEXT,
  worker_started_at TEXT,
  heartbeat_at TEXT,
  started_at TEXT,
  finished_at TEXT,
  exit_code INTEGER,
  last_error TEXT,
  archived_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_runs_active_worktree
  ON agent_runs(worktree_path)
  WHERE state NOT IN ('exited', 'archived');

CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_runs_active_session
  ON agent_runs(session_id)
  WHERE session_id IS NOT NULL
    AND state NOT IN ('exited', 'archived');

CREATE TABLE IF NOT EXISTS control_leases (
  run_id TEXT PRIMARY KEY,
  controller TEXT NOT NULL,
  generation INTEGER NOT NULL,
  token_hash TEXT NOT NULL,
  acquired_at TEXT NOT NULL,
  renewed_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES agent_runs(id)
);

CREATE TABLE IF NOT EXISTS lifecycle_counters (
  run_id TEXT PRIMARY KEY,
  value INTEGER NOT NULL,
  FOREIGN KEY(run_id) REFERENCES agent_runs(id)
);

CREATE TABLE IF NOT EXISTS lifecycle_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  source TEXT NOT NULL,
  controller_generation INTEGER,
  detail TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(run_id, seq),
  FOREIGN KEY(run_id) REFERENCES agent_runs(id)
);

CREATE TABLE IF NOT EXISTS inspection_forks (
  id TEXT PRIMARY KEY,
  source_run_id TEXT NOT NULL,
  session_id TEXT NOT NULL UNIQUE,
  worktree_path TEXT NOT NULL,
  created_at TEXT NOT NULL,
  closed_at TEXT,
  FOREIGN KEY(source_run_id) REFERENCES agent_runs(id)
);

CREATE TABLE IF NOT EXISTS coordinator_sessions (
  id TEXT PRIMARY KEY,
  project_name TEXT NOT NULL,
  runtime_server_id TEXT NOT NULL,
  session_id TEXT NOT NULL UNIQUE,
  directory TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('active','replaced','unreachable','archived')),
  generation INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  archived_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_coordinator_sessions_active_project
  ON coordinator_sessions(project_name)
  WHERE state IN ('active','unreachable');

CREATE TABLE IF NOT EXISTS topics (
  id TEXT PRIMARY KEY,
  project_name TEXT NOT NULL,
  name TEXT NOT NULL,
  title TEXT NOT NULL,
  goal TEXT NOT NULL,
  coordinator_session_id TEXT NOT NULL,
  coordinator_generation INTEGER NOT NULL,
  branch_name TEXT NOT NULL,
  worktree_path TEXT NOT NULL,
  active_run_id TEXT,
  plan_path TEXT,
  lifecycle_state TEXT NOT NULL CHECK(lifecycle_state IN (
    'proposed','active','ready','enqueued','merged',
    'rejected','cancelled','archived'
  )),
  result_state TEXT NOT NULL CHECK(result_state IN (
    'none','planning','planned','implementing','verifying',
    'ready_for_commit','ready_for_enqueue','rejected'
  )),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  archived_at TEXT,
  FOREIGN KEY(active_run_id) REFERENCES agent_runs(id),
  FOREIGN KEY(coordinator_session_id) REFERENCES coordinator_sessions(id),
  UNIQUE(project_name, name),
  UNIQUE(project_name, branch_name),
  UNIQUE(project_name, worktree_path)
);

CREATE INDEX IF NOT EXISTS idx_topics_project_lifecycle
  ON topics(project_name, lifecycle_state, updated_at);
"""

V2_REQUIRED_TABLES = frozenset(
    {
        "tasks",
        "audit_log",
        "counters",
        "agent_runs",
        "control_leases",
        "lifecycle_counters",
        "lifecycle_events",
        "inspection_forks",
        "coordinator_sessions",
        "topics",
    }
)

V2_REQUIRED_INDEXES = frozenset(
    {
        "idx_tasks_status_priority",
        "idx_tasks_status_seq",
        "idx_tasks_branch",
        "idx_tasks_branch_active",
        "idx_agent_runs_active_worktree",
        "idx_agent_runs_active_session",
        "idx_coordinator_sessions_active_project",
        "idx_topics_project_lifecycle",
    }
)


class SchemaAmbiguousError(OrchError):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message,
            code=ExitCode.DB,
            kind="database_schema_ambiguous",
            details=details,
        )


class SchemaVersionError(OrchError):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message,
            code=ExitCode.DB,
            kind="database_schema_unsupported",
            details=details,
        )


def user_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0])


def set_user_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(f"PRAGMA user_version = {int(version)}")


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {r[0] for r in rows}


def _index_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {r[0] for r in rows}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    # PRAGMA table_info: cid, name, type, notnull, dflt_value, pk
    return {r[1] for r in rows}


def is_empty_db(conn: sqlite3.Connection) -> bool:
    return user_version(conn) == 0 and not _table_names(conn)


def is_v1_shape(conn: sqlite3.Connection) -> bool:
    """Exact v1.1 shape: three tables, required columns and indexes."""
    if user_version(conn) != 0:
        return False
    tables = _table_names(conn)
    if tables != set(V1_TABLE_COLUMNS):
        return False
    for table, expected in V1_TABLE_COLUMNS.items():
        cols = _columns(conn, table)
        if cols != expected:
            return False
    indexes = _index_names(conn)
    if not V1_INDEXES.issubset(indexes):
        return False
    return True


def is_v2_complete(conn: sqlite3.Connection) -> bool:
    if user_version(conn) != SCHEMA_VERSION:
        return False
    tables = _table_names(conn)
    if not V2_REQUIRED_TABLES.issubset(tables):
        return False
    # v1 tables still exact
    for table, expected in V1_TABLE_COLUMNS.items():
        if _columns(conn, table) != expected:
            return False
    # agent_runs must have CHECK-closed state columns present
    agent_cols = _columns(conn, "agent_runs")
    required_agent = {
        "id",
        "project_name",
        "agent_name",
        "branch_name",
        "worktree_path",
        "state",
        "desired_state",
        "observed_state",
        "controller",
        "controller_generation",
        "created_at",
        "updated_at",
    }
    if not required_agent.issubset(agent_cols):
        return False
    indexes = _index_names(conn)
    if not V2_REQUIRED_INDEXES.issubset(indexes):
        return False
    return True


def classify_db(conn: sqlite3.Connection) -> str:
    """
    Return one of: empty | v1 | v2 | unsupported | ambiguous
    """
    ver = user_version(conn)
    tables = _table_names(conn)
    if ver == 0 and not tables:
        return "empty"
    if ver > SCHEMA_VERSION:
        return "unsupported"
    if ver == SCHEMA_VERSION:
        return "v2" if is_v2_complete(conn) else "ambiguous"
    if ver == 0:
        if is_v1_shape(conn):
            return "v1"
        # partial or mismatched v1-looking tables
        if tables & set(V1_TABLE_COLUMNS):
            return "ambiguous"
        if tables:
            return "ambiguous"
        return "empty"
    # ver in (1,) unexpected — treat as ambiguous unless somehow v2 objects present
    return "ambiguous"


def _split_sql_statements(script: str) -> list[str]:
    """Split DDL script into statements. Avoids executescript (which auto-COMMITs)."""
    parts: list[str] = []
    buf: list[str] = []
    for line in script.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        buf.append(line)
        if stripped.endswith(";"):
            stmt = "\n".join(buf).strip()
            if stmt:
                parts.append(stmt)
            buf = []
    tail = "\n".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def _exec_script(conn: sqlite3.Connection, script: str) -> None:
    for stmt in _split_sql_statements(script):
        conn.execute(stmt)


def _apply_v2_objects(conn: sqlite3.Connection) -> None:
    _exec_script(conn, SCHEMA_V2_ADDITIVE_SQL)


def _init_empty_v2(conn: sqlite3.Connection) -> None:
    _exec_script(conn, SCHEMA_V1_SQL)
    _exec_script(conn, SCHEMA_V2_ADDITIVE_SQL)
    set_user_version(conn, SCHEMA_VERSION)


def migrate_to_v2(conn: sqlite3.Connection) -> dict[str, Any]:
    """
    Idempotent migration to schema 2 inside a short BEGIN IMMEDIATE.
    Returns summary: {from, to, action}.
    On failure rolls back; never leaves partial schema committed.
    """
    kind = classify_db(conn)
    if kind == "v2":
        return {"from": SCHEMA_VERSION, "to": SCHEMA_VERSION, "action": "noop"}
    if kind == "unsupported":
        raise SchemaVersionError(
            f"database user_version={user_version(conn)} is newer than supported "
            f"{SCHEMA_VERSION}",
            details={"user_version": user_version(conn)},
        )
    if kind == "ambiguous":
        raise SchemaAmbiguousError(
            "database schema is ambiguous or incomplete; refusing to migrate",
            details={
                "user_version": user_version(conn),
                "tables": sorted(_table_names(conn)),
            },
        )

    # Snapshot v1 rows for post-check when migrating from v1
    snapshot: dict[str, list[tuple[Any, ...]]] | None = None
    if kind == "v1":
        snapshot = {
            "tasks": [
                tuple(r) for r in conn.execute("SELECT * FROM tasks").fetchall()
            ],
            "audit_log": [
                tuple(r) for r in conn.execute("SELECT * FROM audit_log").fetchall()
            ],
            "counters": [
                tuple(r) for r in conn.execute("SELECT * FROM counters").fetchall()
            ],
        }

    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.Error as exc:
        raise DbError(f"BEGIN IMMEDIATE failed: {exc}", details={"error": str(exc)}) from exc

    try:
        if kind == "empty":
            _init_empty_v2(conn)
            action = "init"
            from_ver = 0
        elif kind == "v1":
            _apply_v2_objects(conn)
            set_user_version(conn, SCHEMA_VERSION)
            action = "migrate"
            from_ver = 1
        else:
            raise SchemaAmbiguousError(f"unexpected classify result: {kind}")

        if not is_v2_complete(conn):
            raise DbError(
                "schema 2 self-check failed after migration",
                details={"tables": sorted(_table_names(conn))},
            )

        if snapshot is not None:
            for table, rows in snapshot.items():
                now = [tuple(r) for r in conn.execute(f"SELECT * FROM {table}").fetchall()]
                if now != rows:
                    raise DbError(
                        f"migration altered existing {table} rows",
                        details={"table": table},
                    )

        conn.commit()
        return {"from": from_ver, "to": SCHEMA_VERSION, "action": action}
    except Exception:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        raise


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Ensure DB is at schema 2. Safe to call repeatedly."""
    return migrate_to_v2(conn)
