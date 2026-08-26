"""Topic closed-loop start/ready/enqueue/abandon tests (V14)."""

from __future__ import annotations

from pathlib import Path

from orch.commands.enqueue import cmd_enqueue
from orch.commands.skip import cmd_skip
from orch.commands.topic import (
    coordinator_bind,
    topic_abandon,
    topic_enqueue,
    topic_ready,
    topic_start,
)
from orch.constants import MAIN_WORKTREE_NAME, TARGET_BRANCH
from orch.db import open_project_db
from orch.errors import ValidationError
from tests.helpers.git_fixture import commit_file, run
from tests.helpers.orch_env import OrchEnvTestCase


class TopicStartTests(OrchEnvTestCase):
    def setUp(self) -> None:
        super().setUp()
        coordinator_bind(
            self.project,
            session_id="ses_coord",
            directory=str(self.env.proj),
        )

    def test_start_provisions_worktree_and_stays_proposed_without_session(self) -> None:
        out = topic_start(
            self.project,
            name="auth",
            title="Auth",
            goal="ship",
            branch_name="feat/auth",
            agent_name="coder",
            provision_session=False,
        )
        topic = out["topic"]
        self.assertEqual(topic["lifecycle_state"], "proposed")
        wt = Path(topic["worktree_path"])
        self.assertTrue(wt.is_dir())
        self.assertEqual(
            wt.resolve(), (self.env.proj / "worktrees" / "coder-feat__auth").resolve()
        )
        self.assertTrue(topic["base_commit"])
        self.assertNotEqual(wt.resolve(), (self.env.proj / MAIN_WORKTREE_NAME).resolve())

        again = topic_start(
            self.project,
            name="auth",
            title="Auth",
            goal="ship",
            branch_name="feat/auth",
            agent_name="coder",
            provision_session=False,
        )
        self.assertEqual(again["topic"]["id"], topic["id"])

    def test_rejects_develop_branch(self) -> None:
        with self.assertRaises(ValidationError) as ctx:
            topic_start(
                self.project,
                name="bad",
                title="Bad",
                goal="no",
                branch_name=TARGET_BRANCH,
                agent_name="coder",
                provision_session=False,
            )
        self.assertEqual(ctx.exception.kind, "topic_isolation_required")

    def test_does_not_adopt_existing_worktree_add(self) -> None:
        from orch.commands.worktree_add import cmd_worktree_add

        added = cmd_worktree_add(self.project, "coder", "feat/occupied")
        dest = Path(added["worktree_path"])
        with self.assertRaises(ValidationError) as ctx:
            topic_start(
                self.project,
                name="occ",
                title="Occ",
                goal="no",
                branch_name="feat/occupied",
                agent_name="coder",
                provision_session=False,
            )
        self.assertEqual(ctx.exception.kind, "topic_dest_occupied")
        self.assertTrue(dest.is_dir())


class TopicReadyEnqueueTests(OrchEnvTestCase):
    def setUp(self) -> None:
        super().setUp()
        coordinator_bind(
            self.project,
            session_id="ses_coord",
            directory=str(self.env.proj),
        )
        started = topic_start(
            self.project,
            name="auth",
            title="Auth",
            goal="ship",
            branch_name="feat/auth",
            agent_name="coder",
            provision_session=False,
        )
        self.topic_id = started["topic"]["id"]
        self.wt = Path(started["topic"]["worktree_path"])
        self.agent = "coder"
        self.branch = "feat/auth"

    def _ready(self, sha: str) -> dict:
        return topic_ready(
            self.project,
            self.topic_id,
            verification={"commit_sha": sha, "commands": ["pytest"]},
        )

    def test_ready_requires_real_git_evidence_and_does_not_enqueue(self) -> None:
        sha = commit_file(self.wt, "auth.py", "x = 1\n")
        ready = self._ready(sha)
        self.assertFalse(ready["enqueued"])
        self.assertEqual(ready["lifecycle_state"], "ready")
        self.assertEqual(ready["result_state"], "ready_for_enqueue")
        self.assertEqual(ready["verification"]["commit_sha"], sha)

    def test_ready_rejects_fake_sha(self) -> None:
        commit_file(self.wt, "auth.py", "x = 1\n")
        with self.assertRaises(ValidationError) as ctx:
            self._ready("abc123")
        self.assertEqual(ctx.exception.kind, "topic_verification_sha_mismatch")

    def test_ready_rejects_empty_diff(self) -> None:
        head = run(["git", "rev-parse", "HEAD"], cwd=self.wt).stdout.strip()
        with self.assertRaises(ValidationError) as ctx:
            self._ready(head)
        self.assertEqual(ctx.exception.kind, "topic_empty_diff")

    def test_ready_rejects_dirty_worktree(self) -> None:
        sha = commit_file(self.wt, "auth.py", "x = 1\n")
        (self.wt / "dirty.txt").write_text("nope\n", encoding="utf-8")
        with self.assertRaises(ValidationError) as ctx:
            self._ready(sha)
        self.assertEqual(ctx.exception.kind, "topic_worktree_dirty")

    def test_enqueue_writes_identity_chain_and_freezes_ready(self) -> None:
        sha = commit_file(self.wt, "auth.py", "x = 1\n")
        self._ready(sha)
        enq = topic_enqueue(self.project, self.topic_id)
        self.assertTrue(enq["enqueued"])
        task_id = enq["task_id"]
        conn = open_project_db(self.project, init=True)
        try:
            topic = conn.execute(
                "SELECT * FROM topics WHERE id = ?", (self.topic_id,)
            ).fetchone()
            self.assertEqual(topic["lifecycle_state"], "enqueued")
            self.assertEqual(topic["task_id"], task_id)
            runs = conn.execute(
                """
                SELECT task_id, topic_id FROM agent_runs
                WHERE topic_id = ? AND state NOT IN ('exited', 'archived')
                """,
                (self.topic_id,),
            ).fetchall()
            self.assertEqual(
                len(runs),
                0,
                "no agent_runs exist unless a session was provisioned",
            )
        finally:
            conn.close()

        with self.assertRaises(ValidationError) as ctx:
            self._ready(sha)
        self.assertEqual(ctx.exception.kind, "topic_sha_frozen")

        with self.assertRaises(ValidationError) as ctx:
            cmd_enqueue(self.project, self.agent, self.branch, str(self.wt))
        self.assertEqual(ctx.exception.kind, "topic_enqueue_required")

    def test_frozen_topic_lookup_matches_path_variants(self) -> None:
        import os
        import sys

        from orch.runtime.lifecycle import find_frozen_topic
        from orch.validate import canonical_worktree_path

        sha = commit_file(self.wt, "auth.py", "x = 1\n")
        self._ready(sha)
        topic_enqueue(self.project, self.topic_id)
        stored = canonical_worktree_path(str(self.wt.resolve()))
        conn = open_project_db(self.project, init=True)
        try:
            row = conn.execute(
                "SELECT worktree_path FROM topics WHERE id = ?", (self.topic_id,)
            ).fetchone()
            self.assertEqual(row["worktree_path"], stored)
            sep = "\\" if os.name == "nt" else "/"
            variants = [str(self.wt), self.wt.as_posix(), str(self.wt.resolve()) + sep]
            if sys.platform == "win32":
                raw = str(self.wt.resolve())
                if len(raw) >= 2 and raw[1] == ":":
                    variants.append(raw[0].swapcase() + raw[1:])
            for variant in variants:
                frozen = find_frozen_topic(conn, self.project, variant)
                self.assertIsNotNone(frozen, msg=variant)
                self.assertEqual(frozen["topic_id"], self.topic_id)
        finally:
            conn.close()

    def test_enqueue_writes_agent_run_identity_when_run_exists(self) -> None:
        conn = open_project_db(self.project, init=True)
        try:
            conn.execute(
                """
                INSERT INTO agent_runs (
                  id, project_name, agent_name, branch_name, worktree_path,
                  runtime_kind, runtime_server_id, state, desired_state,
                  observed_state, controller, controller_generation,
                  created_at, updated_at, topic_id
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "run_auth",
                    self.project,
                    self.agent,
                    self.branch,
                    str(self.wt.resolve()),
                    "opencode",
                    "srv",
                    "running",
                    "running",
                    "busy",
                    "agent",
                    0,
                    "t",
                    "t",
                    self.topic_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        sha = commit_file(self.wt, "auth.py", "x = 1\n")
        self._ready(sha)
        enq = topic_enqueue(self.project, self.topic_id)
        conn = open_project_db(self.project, init=True)
        try:
            runs = conn.execute(
                """
                SELECT task_id, topic_id FROM agent_runs
                WHERE id = 'run_auth'
                """,
            ).fetchall()
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]["task_id"], enq["task_id"])
            self.assertEqual(runs[0]["topic_id"], self.topic_id)
        finally:
            conn.close()

    def test_ready_after_enqueue_blocked_only_while_pending(self) -> None:
        sha = commit_file(self.wt, "auth.py", "x = 1\n")
        self._ready(sha)
        topic_enqueue(self.project, self.topic_id)
        new_sha = commit_file(self.wt, "auth2.py", "y = 2\n")
        with self.assertRaises(ValidationError) as ctx:
            self._ready(new_sha)
        self.assertEqual(ctx.exception.kind, "topic_sha_frozen")

        conn = open_project_db(self.project, init=True)
        try:
            conn.execute(
                "UPDATE tasks SET status = 'conflict' WHERE id = ("
                "SELECT task_id FROM topics WHERE id = ?)",
                (self.topic_id,),
            )
            conn.commit()
        finally:
            conn.close()
        again = self._ready(new_sha)
        self.assertEqual(again["verification"]["commit_sha"], new_sha)
        conn = open_project_db(self.project, init=True)
        try:
            row = conn.execute(
                "SELECT lifecycle_state FROM topics WHERE id = ?",
                (self.topic_id,),
            ).fetchone()
            self.assertEqual(row["lifecycle_state"], "enqueued")
        finally:
            conn.close()

    def test_ready_rejects_cancelled_and_abandon_writes_db(self) -> None:
        sha = commit_file(self.wt, "auth.py", "x = 1\n")
        self._ready(sha)
        topic_abandon(self.project, self.topic_id)
        conn = open_project_db(self.project, init=True)
        try:
            row = conn.execute(
                "SELECT lifecycle_state FROM topics WHERE id = ?",
                (self.topic_id,),
            ).fetchone()
            self.assertEqual(row["lifecycle_state"], "cancelled")
        finally:
            conn.close()
        with self.assertRaises(ValidationError) as ctx:
            self._ready(sha)
        self.assertEqual(ctx.exception.kind, "topic_ready_illegal")

    def test_start_continue_does_not_fake_exit_starting_run(self) -> None:
        conn = open_project_db(self.project, init=True)
        try:
            conn.execute(
                """
                INSERT INTO agent_runs (
                  id, project_name, agent_name, branch_name, worktree_path,
                  runtime_kind, runtime_server_id, state, desired_state,
                  observed_state, controller, controller_generation,
                  created_at, updated_at, topic_id
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "run_live",
                    self.project,
                    "coder",
                    self.branch,
                    str(self.wt.resolve()),
                    "opencode",
                    "srv",
                    "starting",
                    "running",
                    "starting",
                    "agent",
                    0,
                    "t",
                    "t",
                    self.topic_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        again = topic_start(
            self.project,
            name="auth",
            title="Auth",
            goal="ship",
            branch_name="feat/auth",
            agent_name="coder",
            provision_session=False,
        )
        self.assertEqual(again["topic"]["id"], self.topic_id)
        conn = open_project_db(self.project, init=True)
        try:
            row = conn.execute(
                "SELECT state, finished_at FROM agent_runs WHERE id = 'run_live'"
            ).fetchone()
            self.assertEqual(row["state"], "starting")
            self.assertIsNone(row["finished_at"])
        finally:
            conn.close()

    def test_skip_clears_task_pointer_then_bare_enqueue_allowed(self) -> None:
        sha = commit_file(self.wt, "auth.py", "x = 1\n")
        self._ready(sha)
        enq = topic_enqueue(self.project, self.topic_id)
        skipped_id = enq["task_id"]
        cmd_skip(self.project, skipped_id, reason="drop")
        conn = open_project_db(self.project, init=True)
        try:
            topic = conn.execute(
                "SELECT * FROM topics WHERE id = ?", (self.topic_id,)
            ).fetchone()
            self.assertEqual(topic["lifecycle_state"], "rejected")
            self.assertIsNone(topic["task_id"])
        finally:
            conn.close()
        bare = cmd_enqueue(self.project, self.agent, self.branch, str(self.wt))
        self.assertNotEqual(bare["task_id"], skipped_id)

    def test_abandon_cancels_ready_topic(self) -> None:
        sha = commit_file(self.wt, "auth.py", "x = 1\n")
        self._ready(sha)
        out = topic_abandon(self.project, self.topic_id)
        self.assertTrue(out["abandoned"])
        self.assertEqual(out["lifecycle_state"], "cancelled")


class TopicActiveRunClearTests(OrchEnvTestCase):
    def setUp(self) -> None:
        super().setUp()
        coordinator_bind(
            self.project,
            session_id="ses_coord",
            directory=str(self.env.proj),
        )
        started = topic_start(
            self.project,
            name="auth",
            title="Auth",
            goal="ship",
            branch_name="feat/auth",
            agent_name="coder",
            provision_session=False,
        )
        self.topic_id = started["topic"]["id"]
        self.wt = started["topic"]["worktree_path"]

    def _attach_running(self, run_id: str) -> None:
        conn = open_project_db(self.project, init=True)
        try:
            conn.execute(
                """
                INSERT INTO agent_runs (
                  id, project_name, agent_name, branch_name, worktree_path,
                  runtime_kind, runtime_server_id, state, desired_state,
                  observed_state, controller, controller_generation,
                  created_at, updated_at, topic_id
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    run_id,
                    self.project,
                    "coder",
                    "feat/auth",
                    self.wt,
                    "opencode",
                    "srv",
                    "running",
                    "running",
                    "busy",
                    "agent",
                    0,
                    "t",
                    "t",
                    self.topic_id,
                ),
            )
            conn.execute(
                "UPDATE topics SET active_run_id = ? WHERE id = ?",
                (run_id, self.topic_id),
            )
            conn.commit()
        finally:
            conn.close()

    def test_stop_clears_active_run_id_and_open_is_directory(self) -> None:
        from orch.commands.agent_lifecycle import cmd_agent_stop
        from orch.commands.topic import topic_open

        self._attach_running("run_stop")
        cmd_agent_stop(self.project, "run_stop")
        conn = open_project_db(self.project, init=True)
        try:
            row = conn.execute(
                "SELECT active_run_id FROM topics WHERE id = ?", (self.topic_id,)
            ).fetchone()
            self.assertIsNone(row["active_run_id"])
        finally:
            conn.close()
        opened = topic_open(self.project, self.topic_id)
        self.assertEqual(opened["mode"], "topic_open")

    def test_archive_clears_active_run_id(self) -> None:
        from orch.commands.agent_lifecycle import cmd_agent_archive

        conn = open_project_db(self.project, init=True)
        try:
            conn.execute(
                """
                INSERT INTO agent_runs (
                  id, project_name, agent_name, branch_name, worktree_path,
                  runtime_kind, runtime_server_id, state, desired_state,
                  observed_state, controller, controller_generation,
                  created_at, updated_at, topic_id
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "run_arch",
                    self.project,
                    "coder",
                    "feat/auth",
                    self.wt,
                    "opencode",
                    "srv",
                    "exited",
                    "stopped",
                    "exited",
                    "none",
                    0,
                    "t",
                    "t",
                    self.topic_id,
                ),
            )
            conn.execute(
                "UPDATE topics SET active_run_id = ? WHERE id = ?",
                ("run_arch", self.topic_id),
            )
            conn.commit()
        finally:
            conn.close()
        cmd_agent_archive(self.project, "run_arch")
        conn = open_project_db(self.project, init=True)
        try:
            row = conn.execute(
                "SELECT active_run_id FROM topics WHERE id = ?", (self.topic_id,)
            ).fetchone()
            self.assertIsNone(row["active_run_id"])
        finally:
            conn.close()

