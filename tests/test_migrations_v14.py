"""V14 schema 4: topic closed-loop columns without ALTER tasks."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from orch.db import connect
from orch.migrations import (
    SCHEMA_VERSION,
    SCHEMA_V3,
    SCHEMA_V4,
    classify_db,
    ensure_schema,
    is_v3_complete,
    is_v4_complete,
    migrate_to_v3,
    migrate_to_v4,
    user_version,
    _columns,
    _table_names,
)


class MigrationV14Tests(unittest.TestCase):
    def test_empty_db_inits_schema_4(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            conn = connect(Path(td) / "empty.db")
            result = ensure_schema(conn)
            self.assertEqual(result["action"], "init")
            self.assertEqual(user_version(conn), SCHEMA_V4)
            self.assertEqual(SCHEMA_VERSION, SCHEMA_V4)
            self.assertTrue(is_v3_complete(conn), "v4 DB must still count as v3-complete")
            self.assertTrue(is_v4_complete(conn))
            self.assertEqual(classify_db(conn), "v4")
            tables = _table_names(conn)
            self.assertNotIn("topic_events", tables)
            topic_cols = _columns(conn, "topics")
            for col in (
                "agent_name",
                "task_id",
                "last_step",
                "last_error",
                "base_commit",
                "verification_record_id",
            ):
                self.assertIn(col, topic_cols)
            self.assertIn("topic_id", _columns(conn, "agent_runs"))
            task_cols = _columns(conn, "tasks")
            self.assertNotIn("topic_id", task_cols)
            conn.close()

    def test_v3_to_v4_preserves_task_rows(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            conn = connect(Path(td) / "v3.db")
            migrate_to_v3(conn)
            self.assertEqual(user_version(conn), SCHEMA_V3)
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
            r1 = migrate_to_v4(conn)
            self.assertEqual(r1["action"], "migrate")
            self.assertEqual(user_version(conn), SCHEMA_V4)
            after = [tuple(r) for r in conn.execute("SELECT * FROM tasks").fetchall()]
            self.assertEqual(before, after)
            self.assertTrue(is_v3_complete(conn))
            self.assertTrue(is_v4_complete(conn))
            conn.close()

    def test_migrate_v4_twice_noop(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            conn = connect(Path(td) / "n.db")
            migrate_to_v4(conn)
            second = migrate_to_v4(conn)
            self.assertEqual(second["action"], "noop")
            self.assertEqual(user_version(conn), SCHEMA_V4)
            conn.close()

    def test_migrate_to_v3_on_v4_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            conn = connect(Path(td) / "v4.db")
            migrate_to_v4(conn)
            r = migrate_to_v3(conn)
            self.assertEqual(r["action"], "noop")
            self.assertEqual(user_version(conn), SCHEMA_V4)
            conn.close()

    def test_direct_migrate_to_v3_still_stops_at_3(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            conn = connect(Path(td) / "stop3.db")
            r = migrate_to_v3(conn)
            self.assertEqual(r["action"], "init")
            self.assertEqual(user_version(conn), SCHEMA_V3)
            self.assertTrue(is_v3_complete(conn))
            self.assertFalse(is_v4_complete(conn))
            self.assertEqual(classify_db(conn), "v3")
            conn.close()


if __name__ == "__main__":
    unittest.main()
