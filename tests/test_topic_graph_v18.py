"""V18 topic identity graph, base drift, saga doctor, skip detaches live runs."""

from __future__ import annotations

from pathlib import Path

from orch.commands.merge import cmd_merge
from orch.commands.skip import cmd_skip
from orch.commands.topic import (
    coordinator_bind,
    topic_abandon,
    topic_enqueue,
    topic_ready,
    topic_start,
)
from orch.constants import BARE_DIR_NAME, TARGET_BRANCH
from orch.db import open_project_db
from orch.errors import OrchError, ValidationError
from orch.git.ref import run_git_ref
from orch.topic_graph import assert_topic_graph, doctor_report
from orch.util import utc_now_iso
from tests.helpers.git_fixture import commit_file
from tests.helpers.orch_env import OrchEnvTestCase


class TopicGraphV18Tests(OrchEnvTestCase):
    def setUp(self) -> None:
        super().setUp()
        coordinator_bind(
            self.project,
            session_id="ses_coord",
            directory=str(self.env.proj),
        )

    def _start(self) -> str:
        out = topic_start(
            self.project,
            name="auth",
            title="Auth",
            goal="ship",
            branch_name="feat/auth",
            agent_name="coder",
            provision_session=False,
        )
        return str(out["topic"]["id"])

    def _insert_live_run(
        self, topic_id: str, wt: Path, run_id: str = "run_live"
    ) -> None:
        now = utc_now_iso()
        conn = open_project_db(self.project, init=False)
        try:
            conn.execute(
                """
                INSERT INTO agent_runs(
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
                    str(wt),
                    "opencode",
                    "srv",
                    "running",
                    "running",
                    "running",
                    "agent",
                    1,
                    now,
                    now,
                    topic_id,
                ),
            )
            conn.execute(
                "UPDATE topics SET active_run_id = ? WHERE id = ?",
                (run_id, topic_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _ready(self, topic_id: str, wt: Path) -> str:
        sha = commit_file(wt, "a.txt", "x\n", "add")
        return topic_ready(
            self.project,
            topic_id,
            verification={"commit_sha": sha, "commands": ["git status"]},
        )["verification"]["commit_sha"]

    def test_proposed_with_task_id_is_graph_conflict(self) -> None:
        topic_id = self._start()
        conn = open_project_db(self.project, init=False)
        try:
            conn.execute(
                "UPDATE topics SET task_id = 'ghost' WHERE id = ?",
                (topic_id,),
            )
            conn.commit()
            with self.assertRaises(ValidationError) as ctx:
                assert_topic_graph(conn, topic_id)
            self.assertEqual(ctx.exception.kind, "topic_graph_conflict")
        finally:
            conn.close()

    def test_live_run_without_topic_is_not_a_graph_conflict(self) -> None:
        topic_id = self._start()
        conn = open_project_db(self.project, init=False)
        try:
            now = utc_now_iso()
            conn.execute(
                """
                INSERT INTO agent_runs(
                  id, project_name, agent_name, branch_name, worktree_path,
                  runtime_kind, runtime_server_id, state, desired_state,
                  observed_state, controller, controller_generation,
                  created_at, updated_at, topic_id
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, NULL)
                """,
                (
                    "run_naked",
                    self.project,
                    "solo",
                    "feat/solo",
                    "/tmp/solo",
                    "opencode",
                    "srv",
                    "running",
                    "running",
                    "running",
                    "agent",
                    1,
                    now,
                    now,
                ),
            )
            conn.commit()
            assert_topic_graph(conn, topic_id)
            report = doctor_report(conn, self.project)
            orphan_ids = [o.get("id") for o in report["orphans"]]
            self.assertNotIn("run_naked", orphan_ids)
        finally:
            conn.close()

    def test_ready_records_verified_against_base(self) -> None:
        topic_id = self._start()
        conn = open_project_db(self.project, init=False)
        try:
            row = conn.execute(
                "SELECT worktree_path FROM topics WHERE id = ?", (topic_id,)
            ).fetchone()
            wt = Path(row["worktree_path"])
        finally:
            conn.close()
        self._ready(topic_id, wt)
        bare = self.env.proj / BARE_DIR_NAME
        develop = run_git_ref(
            ["rev-parse", TARGET_BRANCH], bare, check=True
        ).stdout.strip()
        conn = open_project_db(self.project, init=False)
        try:
            row = conn.execute(
                "SELECT verified_against_base, provision_phase FROM topics WHERE id = ?",
                (topic_id,),
            ).fetchone()
            self.assertEqual(row["verified_against_base"], develop)
            self.assertEqual(row["provision_phase"], "done")
        finally:
            conn.close()

    def test_enqueue_rejects_base_drift(self) -> None:
        topic_id = self._start()
        conn = open_project_db(self.project, init=False)
        try:
            wt = Path(
                conn.execute(
                    "SELECT worktree_path FROM topics WHERE id = ?", (topic_id,)
                ).fetchone()["worktree_path"]
            )
        finally:
            conn.close()
        self._ready(topic_id, wt)
        conn = open_project_db(self.project, init=False)
        try:
            conn.execute(
                "UPDATE topics SET verified_against_base = ? WHERE id = ?",
                ("0" * 40, topic_id),
            )
            conn.commit()
        finally:
            conn.close()
        with self.assertRaises(ValidationError) as ctx:
            topic_enqueue(self.project, topic_id)
        self.assertEqual(ctx.exception.kind, "topic_base_drift")

    def test_skip_detaches_live_run(self) -> None:
        topic_id = self._start()
        conn = open_project_db(self.project, init=False)
        try:
            wt = Path(
                conn.execute(
                    "SELECT worktree_path FROM topics WHERE id = ?", (topic_id,)
                ).fetchone()["worktree_path"]
            )
        finally:
            conn.close()
        self._ready(topic_id, wt)
        enq = topic_enqueue(self.project, topic_id)
        self._insert_live_run(topic_id, wt)
        cmd_skip(self.project, enq["task_id"], reason="nope")
        conn = open_project_db(self.project, init=False)
        try:
            topic = conn.execute(
                "SELECT active_run_id, lifecycle_state FROM topics WHERE id = ?",
                (topic_id,),
            ).fetchone()
            run = conn.execute(
                "SELECT topic_id FROM agent_runs WHERE id = ?",
                ("run_live",),
            ).fetchone()
            self.assertEqual(topic["lifecycle_state"], "rejected")
            self.assertIsNone(topic["active_run_id"])
            self.assertIsNone(run["topic_id"])
        finally:
            conn.close()

    def test_doctor_lists_provision_needs_recovery(self) -> None:
        topic_id = self._start()
        conn = open_project_db(self.project, init=False)
        try:
            conn.execute(
                "UPDATE topics SET provision_phase = 'needs_recovery' WHERE id = ?",
                (topic_id,),
            )
            conn.commit()
            report = doctor_report(conn, self.project)
            ids = [r["topic_id"] for r in report["provision_needs_recovery"]]
            self.assertIn(topic_id, ids)
        finally:
            conn.close()

    def test_abandon_detaches_live_run(self) -> None:
        topic_id = self._start()
        conn = open_project_db(self.project, init=False)
        try:
            wt = Path(
                conn.execute(
                    "SELECT worktree_path FROM topics WHERE id = ?", (topic_id,)
                ).fetchone()["worktree_path"]
            )
        finally:
            conn.close()
        self._insert_live_run(topic_id, wt)
        topic_abandon(self.project, topic_id)
        conn = open_project_db(self.project, init=False)
        try:
            topic = conn.execute(
                "SELECT active_run_id, lifecycle_state FROM topics WHERE id = ?",
                (topic_id,),
            ).fetchone()
            run = conn.execute(
                "SELECT topic_id FROM agent_runs WHERE id = ?",
                ("run_live",),
            ).fetchone()
            self.assertEqual(topic["lifecycle_state"], "cancelled")
            self.assertIsNone(topic["active_run_id"])
            self.assertIsNone(run["topic_id"])
            assert_topic_graph(conn, topic_id)
        finally:
            conn.close()

    def test_merge_graph_conflict_recovers_without_rolling_back_git(self) -> None:
        topic_id = self._start()
        conn = open_project_db(self.project, init=False)
        try:
            wt = Path(
                conn.execute(
                    "SELECT worktree_path FROM topics WHERE id = ?", (topic_id,)
                ).fetchone()["worktree_path"]
            )
        finally:
            conn.close()
        self._ready(topic_id, wt)
        enq = topic_enqueue(self.project, topic_id)
        self._insert_live_run(topic_id, wt)
        with self.assertRaises(OrchError) as ctx:
            cmd_merge(self.project, once=True)
        self.assertEqual(ctx.exception.kind, "merge_aborted_recovery_required")
        self.assertEqual(ctx.exception.details.get("status"), "recovery_required")
        bare = self.env.proj / BARE_DIR_NAME
        anc = run_git_ref(
            ["merge-base", "--is-ancestor", enq["source_commit"], TARGET_BRANCH],
            bare,
        )
        self.assertEqual(anc.returncode, 0)
        conn = open_project_db(self.project, init=False)
        try:
            task = conn.execute(
                "SELECT status FROM tasks WHERE id = ?", (enq["task_id"],)
            ).fetchone()
            topic = conn.execute(
                "SELECT lifecycle_state FROM topics WHERE id = ?", (topic_id,)
            ).fetchone()
            self.assertEqual(task["status"], "recovery_required")
            self.assertEqual(topic["lifecycle_state"], "enqueued")
        finally:
            conn.close()
