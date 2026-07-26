"""Phase 4 unit tests: cleanup guards, hooks, topic, takeover concurrency."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from orch.db import connect, init_schema
from orch.errors import ValidationError
from orch.runtime.cleanup_guard import runtime_prune_blockers
from orch.runtime.hooks import HOOK_ALLOWLIST, run_hook, validate_hook_argv
from orch.commands.topic import coordinator_bind, topic_list, topic_ready, topic_start


class CleanupGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "t.db"
        self.conn = connect(self.db)
        init_schema(self.conn)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_blocks_active_run_and_skipped_task(self) -> None:
        self.conn.execute(
            """
            INSERT INTO agent_runs (
              id, project_name, agent_name, branch_name, worktree_path,
              runtime_kind, runtime_server_id, state, desired_state,
              observed_state, controller, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "run_1",
                "p",
                "a",
                "b",
                "/wt",
                "opencode",
                "srv",
                "running",
                "running",
                "busy",
                "agent",
                "t",
                "t",
            ),
        )
        self.conn.commit()
        blockers = runtime_prune_blockers(
            self.conn, worktree_path="/wt", task_status="skipped"
        )
        codes = {b["code"] for b in blockers}
        self.assertIn("active_or_unresolved_run", codes)
        self.assertIn("task_skipped_retained", codes)


class HookTests(unittest.TestCase):
    def test_allowlist_and_shell_forbidden(self) -> None:
        self.assertIn("BeforeWorktreeRemove", HOOK_ALLOWLIST)
        with self.assertRaises(ValidationError):
            validate_hook_argv(["cmd.exe", "/c", "echo hi"])
        with self.assertRaises(ValidationError):
            validate_hook_argv(["powershell", "-Command", "echo 1"])

    def test_nonblocking_failure(self) -> None:
        result = run_hook(
            "AgentFailed",
            argv=[sys.executable, "-c", "import sys; sys.exit(2)"],
            payload={"password": "should-redact", "run_id": "r1"},
            blocking=False,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["exit_code"], 2)

    def test_blocking_failure_raises(self) -> None:
        with self.assertRaises(ValidationError) as ctx:
            run_hook(
                "BeforeWorktreeRemove",
                argv=[sys.executable, "-c", "import sys; sys.exit(1)"],
                payload={"worktree_path": "/x"},
                blocking=True,
            )
        self.assertEqual(ctx.exception.kind, "hook_blocking_failed")


class TopicTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.project = "topicproj"
        self.db_dir = self.home / ".orchestrator" / "data" / self.project
        self.db_dir.mkdir(parents=True)
        self.db_path = self.db_dir / "orchestrator.db"
        self.lock_path = self.db_dir / "project.lock"
        self.patches = [
            mock.patch(
                "orch.constants.project_db_path", return_value=self.db_path
            ),
            mock.patch(
                "orch.constants.project_data_dir", return_value=self.db_dir
            ),
            mock.patch(
                "orch.constants.project_lock_path", return_value=self.lock_path
            ),
            mock.patch(
                "orch.commands.topic.project_lock_path", return_value=self.lock_path
            ),
        ]
        for p in self.patches:
            p.start()
        conn = connect(self.db_path)
        init_schema(conn)
        conn.close()

    def tearDown(self) -> None:
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_coordinator_and_topic_flow(self) -> None:
        with mock.patch("orch.commands.topic.load_registry", return_value={"server_id": "srv", "base_url": "http://127.0.0.1:4096"}):
            coord = coordinator_bind(
                self.project,
                session_id="ses_coord",
                directory="E:/proj",
            )
            self.assertEqual(coord["coordinator"]["state"], "active")
            started = topic_start(
                self.project,
                name="auth",
                title="Auth",
                goal="ship auth",
                branch_name="topic/auth",
                worktree_path="E:/proj/worktrees/auth",
            )
            tid = started["topic"]["id"]
            listed = topic_list(self.project)
            self.assertEqual(len(listed["topics"]), 1)
            ready = topic_ready(
                self.project,
                tid,
                verification={"commit_sha": "abc", "commands": ["pytest"]},
            )
            self.assertFalse(ready["enqueued"])
            self.assertEqual(ready["result_state"], "ready_for_enqueue")


class TakeoverBusyTests(unittest.TestCase):
    def test_pausing_rejects_second_takeover(self) -> None:
        from orch.runtime import takeover as T

        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "t.db"
            conn = connect(db)
            init_schema(conn)
            conn.execute(
                """
                INSERT INTO agent_runs (
                  id, project_name, agent_name, branch_name, worktree_path,
                  runtime_kind, runtime_server_id, session_id, state, desired_state,
                  observed_state, controller, controller_generation,
                  created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "run_x",
                    "p",
                    "a",
                    "b",
                    "/wt",
                    "opencode",
                    "srv",
                    "ses",
                    "pausing",
                    "paused",
                    "busy",
                    "agent",
                    2,
                    "t",
                    "t",
                ),
            )
            conn.commit()
            conn.close()
            with mock.patch("orch.runtime.takeover.open_project_db") as op, \
                 mock.patch("orch.runtime.takeover.acquire") as acq, \
                 mock.patch("orch.runtime.takeover.release"):
                acq.return_value = mock.Mock()
                c2 = connect(db)
                op.return_value = c2
                with self.assertRaises(ValidationError) as ctx:
                    T.direct_takeover("p", "run_x")
                self.assertEqual(ctx.exception.kind, "takeover_busy")
                c2.close()


if __name__ == "__main__":
    unittest.main()
