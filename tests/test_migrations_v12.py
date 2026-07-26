"""V12-002 migration tests (§19.2)."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from orch.db import connect, init_schema
from orch.migrations import (
    SCHEMA_V1_SQL,
    SchemaAmbiguousError,
    SchemaVersionError,
    classify_db,
    ensure_schema,
    is_v2_complete,
    migrate_to_v2,
    user_version,
)


def _exec_v1(conn: sqlite3.Connection) -> None:
    # Use statement splitter path via migrate helpers by executing v1 SQL carefully.
    from orch.migrations import _exec_script

    _exec_script(conn, SCHEMA_V1_SQL)
    conn.commit()


class MigrationTests(unittest.TestCase):
    def test_empty_db_inits_schema_2(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "empty.db"
            conn = connect(db)
            result = migrate_to_v2(conn)
            self.assertEqual(result["action"], "init")
            self.assertEqual(user_version(conn), 2)
            self.assertTrue(is_v2_complete(conn))
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            for name in (
                "tasks",
                "agent_runs",
                "control_leases",
                "lifecycle_events",
                "lifecycle_counters",
                "inspection_forks",
                "coordinator_sessions",
                "topics",
            ):
                self.assertIn(name, tables)
            conn.close()

    def test_v1_migrate_idempotent_preserves_rows(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "v1.db"
            conn = connect(db)
            _exec_v1(conn)
            with conn:
                conn.execute(
                    """
                    INSERT INTO tasks(
                      id, agent_name, branch_name, worktree_path, priority, status,
                      submitted_at, source_commit, target_head_before, queue_seq
                    ) VALUES (?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "t1",
                        "a",
                        "feat/x",
                        "/tmp/wt",
                        1,
                        "pending",
                        "2020-01-01T00:00:00Z",
                        "abc",
                        "def",
                        1,
                    ),
                )
                conn.execute(
                    "INSERT INTO audit_log(task_id, action, detail, created_at) "
                    "VALUES (?,?,?,?)",
                    ("t1", "enqueue", "ok", "2020-01-01T00:00:00Z"),
                )
                conn.execute(
                    "UPDATE counters SET value = 7 WHERE name = 'queue_seq'"
                )

            tasks_before = [
                tuple(r) for r in conn.execute("SELECT * FROM tasks").fetchall()
            ]
            audit_before = [
                tuple(r) for r in conn.execute("SELECT * FROM audit_log").fetchall()
            ]
            counters_before = [
                tuple(r) for r in conn.execute("SELECT * FROM counters").fetchall()
            ]

            r1 = migrate_to_v2(conn)
            self.assertEqual(r1["action"], "migrate")
            self.assertEqual(user_version(conn), 2)

            r2 = migrate_to_v2(conn)
            self.assertEqual(r2["action"], "noop")

            tasks_after = [
                tuple(r) for r in conn.execute("SELECT * FROM tasks").fetchall()
            ]
            audit_after = [
                tuple(r) for r in conn.execute("SELECT * FROM audit_log").fetchall()
            ]
            counters_after = [
                tuple(r) for r in conn.execute("SELECT * FROM counters").fetchall()
            ]
            self.assertEqual(tasks_before, tasks_after)
            self.assertEqual(audit_before, audit_after)
            self.assertEqual(counters_before, counters_after)
            conn.close()

    def test_ambiguous_partial_v1_refuses_and_preserves_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "partial.db"
            conn = connect(db)
            conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY)")
            conn.commit()
            tables_before = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            ver_before = user_version(conn)
            with self.assertRaises(SchemaAmbiguousError):
                migrate_to_v2(conn)
            tables_after = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            self.assertEqual(tables_before, tables_after)
            self.assertEqual(ver_before, user_version(conn))
            self.assertNotIn("agent_runs", tables_after)
            conn.close()

    def test_unsupported_higher_version(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "future.db"
            conn = connect(db)
            _exec_v1(conn)
            conn.execute("PRAGMA user_version = 99")
            conn.commit()
            tables_before = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            with self.assertRaises(SchemaVersionError):
                migrate_to_v2(conn)
            tables_after = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            self.assertEqual(user_version(conn), 99)
            self.assertEqual(tables_before, tables_after)
            self.assertNotIn("agent_runs", tables_after)
            conn.close()

    def test_heterogeneous_same_name_refused(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "hetero.db"
            conn = connect(db)
            # Same table names as v1 but wrong columns
            from orch.migrations import _exec_script

            _exec_script(
                conn,
                """
                CREATE TABLE tasks (id TEXT PRIMARY KEY, weird TEXT);
                CREATE TABLE audit_log (id INTEGER PRIMARY KEY);
                CREATE TABLE counters (name TEXT PRIMARY KEY, value INTEGER);
                """,
            )
            conn.commit()
            self.assertEqual(classify_db(conn), "ambiguous")
            tables_before = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            with self.assertRaises(SchemaAmbiguousError):
                ensure_schema(conn)
            tables_after = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            self.assertEqual(tables_before, tables_after)
            self.assertEqual(user_version(conn), 0)
            self.assertNotIn("agent_runs", tables_after)
            conn.close()

    def test_init_schema_via_db_module(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "t.db"
            conn = connect(db)
            init_schema(conn)
            self.assertEqual(user_version(conn), 2)
            init_schema(conn)  # noop
            self.assertTrue(is_v2_complete(conn))
            conn.close()

    def test_check_rejects_unknown_agent_state(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "chk.db"
            conn = connect(db)
            init_schema(conn)
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    INSERT INTO agent_runs (
                      id, project_name, agent_name, branch_name, worktree_path,
                      runtime_kind, runtime_server_id, state, desired_state,
                      observed_state, controller, created_at, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        "run_x",
                        "p",
                        "a",
                        "b",
                        "/wt",
                        "opencode",
                        "srv",
                        "not_a_real_state",
                        "running",
                        "idle",
                        "none",
                        "t",
                        "t",
                    ),
                )
            conn.close()


if __name__ == "__main__":
    unittest.main()
