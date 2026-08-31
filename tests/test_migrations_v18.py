"""V18 schema 5: idx_topics_task_id, saga/base columns, classify/migrate noop."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from orch.db import connect
from orch.migrations import (
    SCHEMA_V4,
    SCHEMA_V5,
    SCHEMA_VERSION,
    classify_db,
    ensure_schema,
    is_v4_complete,
    is_v5_complete,
    migrate_to_v2,
    migrate_to_v3,
    migrate_to_v4,
    migrate_to_v5,
    user_version,
    _columns,
    _index_names,
    _table_names,
)


def _index_sql(conn: sqlite3.Connection, name: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
        (name,),
    ).fetchone()
    return "" if row is None or row[0] is None else str(row[0])


class MigrationV18Tests(unittest.TestCase):
    def test_empty_db_inits_schema_5(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            conn = connect(Path(td) / "empty.db")
            result = ensure_schema(conn)
            self.assertEqual(result["action"], "init")
            self.assertEqual(user_version(conn), SCHEMA_V5)
            self.assertEqual(SCHEMA_VERSION, SCHEMA_V5)
            self.assertTrue(is_v4_complete(conn))
            self.assertTrue(is_v5_complete(conn))
            self.assertEqual(classify_db(conn), "v5")
            self.assertNotIn("topic_events", _table_names(conn))
            topic_cols = _columns(conn, "topics")
            for col in (
                "provision_op_id",
                "provision_phase",
                "verified_against_base",
            ):
                self.assertIn(col, topic_cols)
            self.assertIn("capability_digest", _columns(conn, "agent_runs"))
            self.assertIn("runtime_version", _columns(conn, "agent_runs"))
            self.assertIn("capability_digest", _columns(conn, "coordinator_sessions"))
            self.assertIn("runtime_version", _columns(conn, "coordinator_sessions"))
            self.assertIn("capability_digest", _columns(conn, "inspection_forks"))
            self.assertIn("nonce", _columns(conn, "inspection_forks"))
            self.assertIn("runtime_version", _columns(conn, "inspection_forks"))
            indexes = _index_names(conn)
            self.assertIn("idx_topics_task_id", indexes)
            self.assertNotIn("idx_topics_active_task", indexes)
            sql = _index_sql(conn, "idx_topics_task_id")
            self.assertIn("UNIQUE", sql.upper())
            compact = " ".join(sql.split())
            self.assertIn("task_id IS NOT NULL", compact)
            self.assertNotIn("topic_id", _columns(conn, "tasks"))
            conn.close()

    def test_is_v5_complete_reads_sqlite_master_sql_not_just_name(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            conn = connect(Path(td) / "fake.db")
            migrate_to_v4(conn)
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DROP INDEX IF EXISTS idx_topics_active_task")
            conn.execute(
                "CREATE INDEX idx_topics_task_id ON topics(task_id)"
            )
            conn.execute("PRAGMA user_version = 5")
            conn.commit()
            self.assertIn("idx_topics_task_id", _index_names(conn))
            self.assertFalse(is_v5_complete(conn))
            conn.close()

    def test_v4_to_v5_preserves_task_rows(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            conn = connect(Path(td) / "v4.db")
            migrate_to_v4(conn)
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
            before = [tuple(r) for r in conn.execute("SELECT * FROM tasks").fetchall()]
            r1 = migrate_to_v5(conn)
            self.assertEqual(r1["action"], "migrate")
            self.assertEqual(user_version(conn), SCHEMA_V5)
            after = [tuple(r) for r in conn.execute("SELECT * FROM tasks").fetchall()]
            self.assertEqual(before, after)
            self.assertTrue(is_v5_complete(conn))
            conn.close()

    def test_migrate_v5_twice_noop(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            conn = connect(Path(td) / "n.db")
            migrate_to_v5(conn)
            second = migrate_to_v5(conn)
            self.assertEqual(second, {"from": SCHEMA_V5, "to": SCHEMA_V5, "action": "noop"})
            self.assertEqual(user_version(conn), SCHEMA_V5)
            conn.close()

    def test_older_migrate_helpers_noop_on_v5(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            conn = connect(Path(td) / "v5.db")
            migrate_to_v5(conn)
            for fn in (migrate_to_v2, migrate_to_v3, migrate_to_v4):
                r = fn(conn)
                self.assertEqual(r["action"], "noop")
                self.assertEqual(user_version(conn), SCHEMA_V5)
            conn.close()

    def test_direct_migrate_to_v4_still_stops_at_4(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            conn = connect(Path(td) / "stop4.db")
            r = migrate_to_v4(conn)
            self.assertEqual(r["action"], "init")
            self.assertEqual(user_version(conn), SCHEMA_V4)
            self.assertTrue(is_v4_complete(conn))
            self.assertFalse(is_v5_complete(conn))
            self.assertEqual(classify_db(conn), "v4")
            conn.close()

    def test_migrate_v5_dry_run_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            conn = connect(Path(td) / "dry.db")
            migrate_to_v4(conn)
            before_ver = user_version(conn)
            before_master = list(
                conn.execute(
                    "SELECT name, sql FROM sqlite_master ORDER BY name"
                ).fetchall()
            )
            result = migrate_to_v5(conn, dry_run=True)
            self.assertEqual(result["action"], "dry_run")
            self.assertEqual(user_version(conn), before_ver)
            after_master = list(
                conn.execute(
                    "SELECT name, sql FROM sqlite_master ORDER BY name"
                ).fetchall()
            )
            self.assertEqual(before_master, after_master)
            conn.close()
