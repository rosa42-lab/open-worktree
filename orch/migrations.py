"""Schema migration framework (v1.1 -> … -> schema 5). V12-002 / V13-005 / V14-001 / V18."""

from __future__ import annotations

import sqlite3
from typing import Any

from orch.errors import DbError, ExitCode, OrchError

SCHEMA_VERSION = 5
SCHEMA_V2 = 2
SCHEMA_V3 = 3
SCHEMA_V4 = 4
SCHEMA_V5 = 5

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

# ---------------------------------------------------------------------------
# schema 3 additive:
#   - verification_records (V13-005 / design §11.4)
#   - promotion_runs / promotion_events / promotion_tasks (V13-006 / §11.1–§11.3)
# ---------------------------------------------------------------------------

SCHEMA_V3_ADDITIVE_SQL = """
CREATE TABLE IF NOT EXISTS verification_records (
  id TEXT PRIMARY KEY,
  project_name TEXT NOT NULL,
  scope TEXT NOT NULL CHECK(scope IN (
    'topic','develop_publish','candidate_publish','master_release'
  )),
  commit_sha TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN (
    'running','passed','failed','expired','superseded'
  )),
  commands_json TEXT NOT NULL,
  results_json TEXT NOT NULL,
  created_by TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  expires_at TEXT,
  topic_id TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_verification_project_commit
  ON verification_records(project_name, commit_sha, status);

CREATE INDEX IF NOT EXISTS idx_verification_topic
  ON verification_records(topic_id, created_at);

CREATE TABLE IF NOT EXISTS promotion_runs (
  id TEXT PRIMARY KEY,
  project_name TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('develop_publish','master_release')),
  mode TEXT NOT NULL CHECK(mode IN ('direct_ff','candidate_pr','promotion_pr')),
  state TEXT NOT NULL CHECK(state IN (
    'created','prechecking','ready','executing',
    'awaiting_checks','awaiting_approval','ready_to_merge',
    'published_pending_sync','master_merged_pending_sync','syncing',
    'succeeded','released','blocked','reconciling',
    'failed_safe_to_retry','manual_required','cancelled'
  )),
  remote_name TEXT NOT NULL,
  provider TEXT NOT NULL,
  source_ref TEXT NOT NULL,
  target_ref TEXT NOT NULL,
  source_sha TEXT NOT NULL,
  target_sha_before TEXT NOT NULL,
  published_sha TEXT,
  observed_target_sha TEXT,
  verification_record_id TEXT,
  post_verification_record_id TEXT,
  external_id TEXT,
  external_url TEXT,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  finished_at TEXT,
  last_error TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_promotion_active_kind
  ON promotion_runs(project_name, kind)
  WHERE state NOT IN ('succeeded','released','cancelled');

CREATE UNIQUE INDEX IF NOT EXISTS idx_promotion_source
  ON promotion_runs(project_name, kind, source_sha)
  WHERE state NOT IN ('cancelled');

CREATE TABLE IF NOT EXISTS promotion_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  promotion_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  source TEXT NOT NULL,
  detail TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(promotion_id, seq),
  FOREIGN KEY(promotion_id) REFERENCES promotion_runs(id)
);

CREATE TABLE IF NOT EXISTS promotion_tasks (
  promotion_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  merged_commit TEXT NOT NULL,
  PRIMARY KEY(promotion_id, task_id),
  FOREIGN KEY(promotion_id) REFERENCES promotion_runs(id),
  FOREIGN KEY(task_id) REFERENCES tasks(id)
);
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

V3_REQUIRED_TABLES = frozenset(
    {
        "verification_records",
        "promotion_runs",
        "promotion_events",
        "promotion_tasks",
    }
)

V3_REQUIRED_INDEXES = frozenset(
    {
        "idx_verification_project_commit",
        "idx_verification_topic",
        "idx_promotion_active_kind",
        "idx_promotion_source",
    }
)

V3_PROMOTION_RUN_COLUMNS = frozenset(
    {
        "id",
        "project_name",
        "kind",
        "mode",
        "state",
        "remote_name",
        "provider",
        "source_ref",
        "target_ref",
        "source_sha",
        "target_sha_before",
        "published_sha",
        "observed_target_sha",
        "verification_record_id",
        "post_verification_record_id",
        "external_id",
        "external_url",
        "created_by",
        "created_at",
        "updated_at",
        "finished_at",
        "last_error",
    }
)

V3_VERIFICATION_COLUMNS = frozenset(
    {
        "id",
        "project_name",
        "scope",
        "commit_sha",
        "status",
        "commands_json",
        "results_json",
        "created_by",
        "started_at",
        "finished_at",
        "expires_at",
        "topic_id",
        "created_at",
    }
)

V4_TOPIC_COLUMNS = frozenset(
    {
        "agent_name",
        "task_id",
        "last_step",
        "last_error",
        "base_commit",
        "verification_record_id",
    }
)

V4_AGENT_RUN_COLUMNS = frozenset({"topic_id"})

V4_REQUIRED_INDEXES = frozenset(
    {
        "idx_agent_runs_active_topic",
        "idx_topics_active_task",
    }
)

V5_TOPIC_COLUMNS = frozenset(
    {
        "provision_op_id",
        "provision_phase",
        "verified_against_base",
    }
)

V5_AGENT_RUN_COLUMNS = frozenset({"capability_digest", "runtime_version"})

V5_COORDINATOR_COLUMNS = frozenset({"capability_digest", "runtime_version"})

V5_FORK_COLUMNS = frozenset({"capability_digest", "nonce", "runtime_version"})


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


def _index_sql(conn: sqlite3.Connection, name: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
        (name,),
    ).fetchone()
    if row is None or row[0] is None:
        return ""
    return str(row[0])


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
    """v2 完整性：user_version 恰好为 2，或已升到更高但 v2 对象齐全。"""
    ver = user_version(conn)
    if ver < SCHEMA_V2:
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


def is_v3_complete(conn: sqlite3.Connection) -> bool:
    if user_version(conn) < SCHEMA_V3:
        return False
    if not is_v2_complete(conn):
        return False
    tables = _table_names(conn)
    if not V3_REQUIRED_TABLES.issubset(tables):
        return False
    # allow column supersets for forward-compat additive columns later
    if not V3_VERIFICATION_COLUMNS.issubset(_columns(conn, "verification_records")):
        return False
    if not V3_PROMOTION_RUN_COLUMNS.issubset(_columns(conn, "promotion_runs")):
        return False
    indexes = _index_names(conn)
    if not V3_REQUIRED_INDEXES.issubset(indexes):
        return False
    return True


def is_v4_complete(conn: sqlite3.Connection) -> bool:
    if user_version(conn) < SCHEMA_V4:
        return False
    if not is_v3_complete(conn):
        return False
    tables = _table_names(conn)
    if "topics" not in tables or "agent_runs" not in tables:
        return False
    if not V4_TOPIC_COLUMNS.issubset(_columns(conn, "topics")):
        return False
    if not V4_AGENT_RUN_COLUMNS.issubset(_columns(conn, "agent_runs")):
        return False
    indexes = _index_names(conn)
    if user_version(conn) >= SCHEMA_V5:
        if "idx_topics_task_id" not in indexes:
            return False
    elif not V4_REQUIRED_INDEXES.issubset(indexes):
        return False
    return True


def is_v5_complete(conn: sqlite3.Connection) -> bool:
    if user_version(conn) < SCHEMA_V5:
        return False
    if not is_v4_complete(conn):
        return False
    if not V5_TOPIC_COLUMNS.issubset(_columns(conn, "topics")):
        return False
    if not V5_AGENT_RUN_COLUMNS.issubset(_columns(conn, "agent_runs")):
        return False
    if not V5_COORDINATOR_COLUMNS.issubset(_columns(conn, "coordinator_sessions")):
        return False
    if not V5_FORK_COLUMNS.issubset(_columns(conn, "inspection_forks")):
        return False
    indexes = _index_names(conn)
    if "idx_topics_active_task" in indexes:
        return False
    sql = _index_sql(conn, "idx_topics_task_id")
    if not sql:
        return False
    if "UNIQUE" not in sql.upper():
        return False
    compact = " ".join(sql.split())
    if "task_id IS NOT NULL" not in compact:
        return False
    return True


def _is_v3_partial_repairable(conn: sqlite3.Connection) -> bool:
    """user_version=3 且 v2+verification 齐全，但缺 promotion_*（V13-005→006 过渡）。"""
    if user_version(conn) != SCHEMA_V3:
        return False
    if not is_v2_complete(conn):
        return False
    tables = _table_names(conn)
    if "verification_records" not in tables:
        return False
    if not V3_VERIFICATION_COLUMNS.issubset(_columns(conn, "verification_records")):
        return False
    return not V3_REQUIRED_TABLES.issubset(tables)


def classify_db(conn: sqlite3.Connection) -> str:
    """
    Return one of: empty | v1 | v2 | v3 | v4 | v5 | unsupported | ambiguous
    """
    ver = user_version(conn)
    tables = _table_names(conn)
    if ver == 0 and not tables:
        return "empty"
    if ver > SCHEMA_VERSION:
        return "unsupported"
    if ver == SCHEMA_V5:
        return "v5" if is_v5_complete(conn) else "ambiguous"
    if ver == SCHEMA_V4:
        return "v4" if is_v4_complete(conn) else "ambiguous"
    if ver == SCHEMA_V3:
        return "v3" if is_v3_complete(conn) else "ambiguous"
    if ver == SCHEMA_V2:
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
    # unexpected intermediate versions
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


def _apply_v3_objects(conn: sqlite3.Connection) -> None:
    _exec_script(conn, SCHEMA_V3_ADDITIVE_SQL)


def _init_empty_v2(conn: sqlite3.Connection) -> None:
    _exec_script(conn, SCHEMA_V1_SQL)
    _exec_script(conn, SCHEMA_V2_ADDITIVE_SQL)
    set_user_version(conn, SCHEMA_V2)


def _init_empty_v3(conn: sqlite3.Connection) -> None:
    _exec_script(conn, SCHEMA_V1_SQL)
    _exec_script(conn, SCHEMA_V2_ADDITIVE_SQL)
    _exec_script(conn, SCHEMA_V3_ADDITIVE_SQL)
    set_user_version(conn, SCHEMA_V3)


def _add_column_if_missing(conn: sqlite3.Connection, table: str, ddl: str) -> None:
    name = ddl.split()[0]
    if name not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def _apply_v4_objects(conn: sqlite3.Connection) -> None:
    _add_column_if_missing(conn, "topics", "agent_name TEXT")
    _add_column_if_missing(conn, "topics", "base_commit TEXT")
    _add_column_if_missing(conn, "topics", "task_id TEXT")
    _add_column_if_missing(conn, "topics", "verification_record_id TEXT")
    _add_column_if_missing(conn, "topics", "last_step TEXT")
    _add_column_if_missing(conn, "topics", "last_error TEXT")
    _add_column_if_missing(conn, "agent_runs", "topic_id TEXT")
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_runs_active_topic
          ON agent_runs(topic_id)
          WHERE topic_id IS NOT NULL
            AND state NOT IN ('exited', 'archived')
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_topics_active_task
          ON topics(task_id)
          WHERE task_id IS NOT NULL
            AND lifecycle_state = 'enqueued'
        """
    )


def _apply_v5_objects(conn: sqlite3.Connection) -> None:
    _add_column_if_missing(conn, "topics", "provision_op_id TEXT")
    _add_column_if_missing(conn, "topics", "provision_phase TEXT")
    _add_column_if_missing(conn, "topics", "verified_against_base TEXT")
    _add_column_if_missing(conn, "agent_runs", "capability_digest TEXT")
    _add_column_if_missing(conn, "agent_runs", "runtime_version TEXT")
    _add_column_if_missing(conn, "coordinator_sessions", "capability_digest TEXT")
    _add_column_if_missing(conn, "coordinator_sessions", "runtime_version TEXT")
    _add_column_if_missing(conn, "inspection_forks", "capability_digest TEXT")
    _add_column_if_missing(conn, "inspection_forks", "nonce TEXT")
    _add_column_if_missing(conn, "inspection_forks", "runtime_version TEXT")
    conn.execute("DROP INDEX IF EXISTS idx_topics_active_task")
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_topics_task_id
          ON topics(task_id)
          WHERE task_id IS NOT NULL
        """
    )


def _init_empty_v4(conn: sqlite3.Connection) -> None:
    _exec_script(conn, SCHEMA_V1_SQL)
    _exec_script(conn, SCHEMA_V2_ADDITIVE_SQL)
    _exec_script(conn, SCHEMA_V3_ADDITIVE_SQL)
    _apply_v4_objects(conn)
    set_user_version(conn, SCHEMA_V4)


def _init_empty_v5(conn: sqlite3.Connection) -> None:
    _exec_script(conn, SCHEMA_V1_SQL)
    _exec_script(conn, SCHEMA_V2_ADDITIVE_SQL)
    _exec_script(conn, SCHEMA_V3_ADDITIVE_SQL)
    _apply_v4_objects(conn)
    _apply_v5_objects(conn)
    set_user_version(conn, SCHEMA_V5)


def migrate_to_v2(conn: sqlite3.Connection) -> dict[str, Any]:
    """
    Idempotent migration to schema 2 inside a short BEGIN IMMEDIATE.
    Returns summary: {from, to, action}.
    On failure rolls back; never leaves partial schema committed.
    """
    kind = classify_db(conn)
    if kind in ("v2", "v3", "v4", "v5"):
        # already at or past v2
        return {"from": user_version(conn), "to": SCHEMA_V2, "action": "noop"}
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
            set_user_version(conn, SCHEMA_V2)
            action = "migrate"
            from_ver = 1
        else:
            raise SchemaAmbiguousError(f"unexpected classify result: {kind}")

        # v2 complete check: temporarily require ver==2 objects (is_v2_complete allows ver>=2)
        if user_version(conn) != SCHEMA_V2 or not is_v2_complete(conn):
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
        return {"from": from_ver, "to": SCHEMA_V2, "action": action}
    except Exception:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        raise


def migrate_to_v3(conn: sqlite3.Connection) -> dict[str, Any]:
    """
    Idempotent migration to schema 3 (verification_records + promotion_*).
    empty/v1/v2 -> v3；已是完整 v3 -> noop；
    V13-005 仅 verification 的 partial v3 -> 补齐 promotion_*。
    """
    kind = classify_db(conn)
    if kind in ("v3", "v4", "v5"):
        return {"from": user_version(conn), "to": SCHEMA_V3, "action": "noop"}
    if kind == "unsupported":
        raise SchemaVersionError(
            f"database user_version={user_version(conn)} is newer than supported "
            f"{SCHEMA_VERSION}",
            details={"user_version": user_version(conn)},
        )
    if kind == "ambiguous":
        if _is_v3_partial_repairable(conn):
            try:
                conn.execute("BEGIN IMMEDIATE")
            except sqlite3.Error as exc:
                raise DbError(
                    f"BEGIN IMMEDIATE failed: {exc}",
                    details={"error": str(exc)},
                ) from exc
            try:
                _apply_v3_objects(conn)
                if not is_v3_complete(conn):
                    raise DbError(
                        "schema 3 self-check failed after partial repair",
                        details={"tables": sorted(_table_names(conn))},
                    )
                conn.commit()
                return {
                    "from": SCHEMA_V3,
                    "to": SCHEMA_V3,
                    "action": "repair_promotion_tables",
                }
            except Exception:
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                raise
        raise SchemaAmbiguousError(
            "database schema is ambiguous or incomplete; refusing to migrate",
            details={
                "user_version": user_version(conn),
                "tables": sorted(_table_names(conn)),
            },
        )

    snapshot: dict[str, list[tuple[Any, ...]]] | None = None
    if kind in ("v1", "v2"):
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
            _init_empty_v3(conn)
            action = "init"
            from_ver = 0
        elif kind == "v1":
            _apply_v2_objects(conn)
            _apply_v3_objects(conn)
            set_user_version(conn, SCHEMA_V3)
            action = "migrate"
            from_ver = 1
        elif kind == "v2":
            _apply_v3_objects(conn)
            set_user_version(conn, SCHEMA_V3)
            action = "migrate"
            from_ver = 2
        else:
            raise SchemaAmbiguousError(f"unexpected classify result: {kind}")

        if not is_v3_complete(conn):
            raise DbError(
                "schema 3 self-check failed after migration",
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
        return {"from": from_ver, "to": SCHEMA_V3, "action": action}
    except Exception:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        raise


def migrate_to_v4(conn: sqlite3.Connection) -> dict[str, Any]:
    """
    Idempotent migration to schema 4 (topic closed-loop columns).
    empty/v1/v2/v3 -> v4；完整 v4 -> noop。不 ALTER tasks，不建 topic_events。
    """
    kind = classify_db(conn)
    if kind in ("v4", "v5"):
        return {"from": user_version(conn), "to": SCHEMA_V4, "action": "noop"}
    if kind == "unsupported":
        raise SchemaVersionError(
            f"database user_version={user_version(conn)} is newer than supported "
            f"{SCHEMA_VERSION}",
            details={"user_version": user_version(conn)},
        )
    if kind == "ambiguous":
        if _is_v3_partial_repairable(conn):
            migrate_to_v3(conn)
            kind = classify_db(conn)
        else:
            raise SchemaAmbiguousError(
                "database schema is ambiguous or incomplete; refusing to migrate",
                details={
                    "user_version": user_version(conn),
                    "tables": sorted(_table_names(conn)),
                },
            )

    snapshot: dict[str, list[tuple[Any, ...]]] | None = None
    if kind in ("v1", "v2", "v3"):
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
        raise DbError(
            f"BEGIN IMMEDIATE failed: {exc}",
            details={"error": str(exc)},
        ) from exc

    try:
        if kind == "empty":
            _init_empty_v4(conn)
            action = "init"
            from_ver = 0
        elif kind == "v1":
            _apply_v2_objects(conn)
            _apply_v3_objects(conn)
            _apply_v4_objects(conn)
            set_user_version(conn, SCHEMA_V4)
            action = "migrate"
            from_ver = 1
        elif kind == "v2":
            _apply_v3_objects(conn)
            _apply_v4_objects(conn)
            set_user_version(conn, SCHEMA_V4)
            action = "migrate"
            from_ver = 2
        elif kind == "v3":
            _apply_v4_objects(conn)
            set_user_version(conn, SCHEMA_V4)
            action = "migrate"
            from_ver = 3
        else:
            raise SchemaAmbiguousError(f"unexpected classify result: {kind}")

        if not is_v4_complete(conn):
            raise DbError(
                "schema 4 self-check failed after migration",
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
        return {"from": from_ver, "to": SCHEMA_V4, "action": action}
    except Exception:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        raise


def migrate_to_v5(
    conn: sqlite3.Connection, *, dry_run: bool = False
) -> dict[str, Any]:
    """
    Idempotent migration to schema 5 (task_id unique index, saga/base, binding cols).
    empty/v1/v2/v3/v4 -> v5；完整 v5 -> noop。不 ALTER tasks，不建 topic_events。
    dry_run 不写盘。
    """
    kind = classify_db(conn)
    if kind == "v5":
        return {"from": SCHEMA_V5, "to": SCHEMA_V5, "action": "noop"}
    if kind == "unsupported":
        raise SchemaVersionError(
            f"database user_version={user_version(conn)} is newer than supported "
            f"{SCHEMA_VERSION}",
            details={"user_version": user_version(conn)},
        )
    if kind == "ambiguous":
        if _is_v3_partial_repairable(conn):
            migrate_to_v3(conn)
            kind = classify_db(conn)
        else:
            raise SchemaAmbiguousError(
                "database schema is ambiguous or incomplete; refusing to migrate",
                details={
                    "user_version": user_version(conn),
                    "tables": sorted(_table_names(conn)),
                },
            )

    if dry_run:
        return {
            "from": user_version(conn),
            "to": SCHEMA_V5,
            "action": "dry_run",
            "from_kind": kind,
        }

    snapshot: dict[str, list[tuple[Any, ...]]] | None = None
    if kind in ("v1", "v2", "v3", "v4"):
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
        raise DbError(
            f"BEGIN IMMEDIATE failed: {exc}",
            details={"error": str(exc)},
        ) from exc

    try:
        if kind == "empty":
            _init_empty_v5(conn)
            action = "init"
            from_ver = 0
        elif kind == "v1":
            _apply_v2_objects(conn)
            _apply_v3_objects(conn)
            _apply_v4_objects(conn)
            _apply_v5_objects(conn)
            set_user_version(conn, SCHEMA_V5)
            action = "migrate"
            from_ver = 1
        elif kind == "v2":
            _apply_v3_objects(conn)
            _apply_v4_objects(conn)
            _apply_v5_objects(conn)
            set_user_version(conn, SCHEMA_V5)
            action = "migrate"
            from_ver = 2
        elif kind == "v3":
            _apply_v4_objects(conn)
            _apply_v5_objects(conn)
            set_user_version(conn, SCHEMA_V5)
            action = "migrate"
            from_ver = 3
        elif kind == "v4":
            _apply_v5_objects(conn)
            set_user_version(conn, SCHEMA_V5)
            action = "migrate"
            from_ver = 4
        else:
            raise SchemaAmbiguousError(f"unexpected classify result: {kind}")

        if not is_v5_complete(conn):
            raise DbError(
                "schema 5 self-check failed after migration",
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
        return {"from": from_ver, "to": SCHEMA_V5, "action": action}
    except Exception:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        raise


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Ensure DB is at schema 5. Safe to call repeatedly."""
    return migrate_to_v5(conn)
