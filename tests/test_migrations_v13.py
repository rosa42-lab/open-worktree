"""V13-005/006 schema 3 migration + PromotionRepository tests."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from orch.db import connect
from orch.errors import ValidationError
from orch.migrations import (
    SCHEMA_V1_SQL,
    SCHEMA_V2,
    SCHEMA_V3,
    SchemaAmbiguousError,
    classify_db,
    ensure_schema,
    is_v2_complete,
    is_v3_complete,
    migrate_to_v2,
    migrate_to_v3,
    user_version,
    _exec_script,
)
from orch.promotion import repo as promo_repo


def _exec_v1(conn: sqlite3.Connection) -> None:
    _exec_script(conn, SCHEMA_V1_SQL)
    conn.commit()


class MigrationV13Tests(unittest.TestCase):
    def test_empty_db_inits_schema_3(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "empty.db"
            conn = connect(db)
            result = ensure_schema(conn)
            self.assertEqual(result["action"], "init")
            self.assertEqual(user_version(conn), SCHEMA_V3)
            self.assertTrue(is_v3_complete(conn))
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            self.assertIn("verification_records", tables)
            self.assertIn("promotion_runs", tables)
            self.assertIn("promotion_events", tables)
            self.assertIn("promotion_tasks", tables)
            self.assertIn("topics", tables)
            conn.close()

    def test_v2_to_v3_idempotent_preserves_rows(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "v2.db"
            conn = connect(db)
            migrate_to_v2(conn)
            self.assertEqual(user_version(conn), SCHEMA_V2)
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
            tasks_before = [
                tuple(r) for r in conn.execute("SELECT * FROM tasks").fetchall()
            ]
            r1 = migrate_to_v3(conn)
            self.assertEqual(r1["action"], "migrate")
            self.assertEqual(r1["from"], 2)
            self.assertEqual(user_version(conn), SCHEMA_V3)
            r2 = migrate_to_v3(conn)
            self.assertEqual(r2["action"], "noop")
            tasks_after = [
                tuple(r) for r in conn.execute("SELECT * FROM tasks").fetchall()
            ]
            self.assertEqual(tasks_before, tasks_after)
            self.assertTrue(is_v3_complete(conn))
            conn.close()

    def test_v1_to_v3(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "v1.db"
            conn = connect(db)
            _exec_v1(conn)
            self.assertEqual(classify_db(conn), "v1")
            result = migrate_to_v3(conn)
            self.assertEqual(result["action"], "migrate")
            self.assertEqual(user_version(conn), SCHEMA_V3)
            self.assertTrue(is_v3_complete(conn))
            conn.close()

    def test_partial_v3_verification_only_repairs_promotion(self) -> None:
        """模拟 V13-005 仅 verification 的库，V13-006 应补齐 promotion_*。"""
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "partial.db"
            conn = connect(db)
            migrate_to_v2(conn)
            # 只应用 verification 表（旧 SCHEMA_V3 形状）
            conn.execute(
                """
                CREATE TABLE verification_records (
                  id TEXT PRIMARY KEY,
                  project_name TEXT NOT NULL,
                  scope TEXT NOT NULL,
                  commit_sha TEXT NOT NULL,
                  status TEXT NOT NULL,
                  commands_json TEXT NOT NULL,
                  results_json TEXT NOT NULL,
                  created_by TEXT NOT NULL,
                  started_at TEXT NOT NULL,
                  finished_at TEXT,
                  expires_at TEXT,
                  topic_id TEXT,
                  created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX idx_verification_project_commit "
                "ON verification_records(project_name, commit_sha, status)"
            )
            conn.execute(
                "CREATE INDEX idx_verification_topic "
                "ON verification_records(topic_id, created_at)"
            )
            conn.execute("PRAGMA user_version = 3")
            conn.commit()
            self.assertTrue(is_v2_complete(conn))
            self.assertEqual(classify_db(conn), "ambiguous")
            result = migrate_to_v3(conn)
            self.assertEqual(result["action"], "repair_promotion_tables")
            self.assertTrue(is_v3_complete(conn))
            conn.close()

    def test_ambiguous_refuses(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "partial.db"
            conn = connect(db)
            conn.execute("CREATE TABLE weird (id TEXT)")
            conn.commit()
            self.assertEqual(classify_db(conn), "ambiguous")
            with self.assertRaises(SchemaAmbiguousError):
                migrate_to_v3(conn)
            conn.close()


class PromotionRepoTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.db = Path(self._td.name) / "p.db"
        self.conn = connect(self.db)
        ensure_schema(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self._td.cleanup()

    def _insert_task(self, task_id: str = "t1") -> None:
        self.conn.execute(
            """
            INSERT INTO tasks(
              id, agent_name, branch_name, worktree_path, priority, status,
              submitted_at, source_commit, target_head_before, queue_seq
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                task_id,
                "a",
                "feat/x",
                "/tmp/wt",
                1,
                "merged",
                "2020-01-01T00:00:00Z",
                "abc",
                "def",
                1,
            ),
        )
        self.conn.commit()

    def test_create_and_get_run(self) -> None:
        run = promo_repo.create_run(
            self.conn,
            project_name="alpha",
            kind="develop_publish",
            mode="direct_ff",
            remote_name="origin",
            provider="github",
            source_ref="develop",
            target_ref="refs/remotes/origin/develop",
            source_sha="a" * 40,
            target_sha_before="b" * 40,
            created_by="test",
        )
        self.conn.commit()
        self.assertTrue(run["id"].startswith("promo_"))
        self.assertEqual(run["state"], "created")
        got = promo_repo.get_run(self.conn, run["id"])
        self.assertEqual(got["source_sha"], "a" * 40)

    def test_rejects_invalid_mode_state(self) -> None:
        with self.assertRaises(ValidationError) as ctx:
            promo_repo.create_run(
                self.conn,
                project_name="alpha",
                kind="develop_publish",
                mode="squash",
                remote_name="origin",
                provider="github",
                source_ref="develop",
                target_ref="origin/develop",
                source_sha="a" * 40,
                target_sha_before="b" * 40,
                created_by="test",
            )
        self.assertEqual(ctx.exception.kind, "promotion_invalid_mode")

    def test_active_kind_uniqueness(self) -> None:
        promo_repo.create_run(
            self.conn,
            project_name="alpha",
            kind="develop_publish",
            mode="direct_ff",
            remote_name="origin",
            provider="github",
            source_ref="develop",
            target_ref="origin/develop",
            source_sha="a" * 40,
            target_sha_before="b" * 40,
            created_by="test",
        )
        self.conn.commit()
        with self.assertRaises(ValidationError) as ctx:
            promo_repo.create_run(
                self.conn,
                project_name="alpha",
                kind="develop_publish",
                mode="direct_ff",
                remote_name="origin",
                provider="github",
                source_ref="develop",
                target_ref="origin/develop",
                source_sha="c" * 40,
                target_sha_before="b" * 40,
                created_by="test",
            )
        self.assertEqual(ctx.exception.kind, "promotion_conflict")

    def test_source_sha_uniqueness_unless_cancelled(self) -> None:
        run = promo_repo.create_run(
            self.conn,
            project_name="alpha",
            kind="develop_publish",
            mode="direct_ff",
            remote_name="origin",
            provider="github",
            source_ref="develop",
            target_ref="origin/develop",
            source_sha="a" * 40,
            target_sha_before="b" * 40,
            created_by="test",
        )
        promo_repo.update_run_fields(self.conn, run["id"], state="cancelled")
        self.conn.commit()
        # 同 source_sha 在 cancelled 后可再建
        run2 = promo_repo.create_run(
            self.conn,
            project_name="alpha",
            kind="develop_publish",
            mode="direct_ff",
            remote_name="origin",
            provider="github",
            source_ref="develop",
            target_ref="origin/develop",
            source_sha="a" * 40,
            target_sha_before="b" * 40,
            created_by="test",
        )
        self.conn.commit()
        self.assertNotEqual(run["id"], run2["id"])

    def test_event_detail_redacts_secrets(self) -> None:
        run = promo_repo.create_run(
            self.conn,
            project_name="alpha",
            kind="develop_publish",
            mode="direct_ff",
            remote_name="origin",
            provider="github",
            source_ref="develop",
            target_ref="origin/develop",
            source_sha="a" * 40,
            target_sha_before="b" * 40,
            created_by="test",
        )
        ev = promo_repo.append_event(
            self.conn,
            promotion_id=run["id"],
            event_type="probe",
            source="test",
            detail={"token": "gho_secret", "sha": "abc"},
        )
        self.conn.commit()
        self.assertIn("<redacted>", ev["detail"] or "")
        self.assertNotIn("gho_secret", ev["detail"] or "")
        events = promo_repo.list_events(self.conn, run["id"])
        self.assertEqual(len(events), 1)

    def test_link_tasks(self) -> None:
        self._insert_task("t1")
        run = promo_repo.create_run(
            self.conn,
            project_name="alpha",
            kind="develop_publish",
            mode="direct_ff",
            remote_name="origin",
            provider="github",
            source_ref="develop",
            target_ref="origin/develop",
            source_sha="a" * 40,
            target_sha_before="b" * 40,
            created_by="test",
        )
        promo_repo.link_tasks(self.conn, run["id"], [("t1", "deadbeef")])
        self.conn.commit()
        links = promo_repo.list_tasks(self.conn, run["id"])
        self.assertEqual(links, [
            {"promotion_id": run["id"], "task_id": "t1", "merged_commit": "deadbeef"}
        ])


if __name__ == "__main__":
    unittest.main()
