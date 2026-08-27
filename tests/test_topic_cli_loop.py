"""V15: Topic closed loop must be reachable through CLI argv without a runtime."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from orch.db import open_project_db
from tests.helpers.git_fixture import commit_file
from tests.helpers.orch_env import OrchEnvTestCase


def _backdate_finished(project: str, task_id: str, hours: int = 48) -> None:
    past = (
        datetime.now(timezone.utc) - timedelta(hours=hours)
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    conn = open_project_db(project, init=False)
    try:
        conn.execute(
            "UPDATE tasks SET finished_at = ? WHERE id = ?",
            (past, task_id),
        )
        conn.commit()
    finally:
        conn.close()


class TopicCliLoopTests(OrchEnvTestCase):
    def setUp(self) -> None:
        super().setUp()
        code, payload = self.env.run_json(
            self.project,
            "coordinator-bind",
            "--session",
            "ses_coord",
            "--directory",
            str(self.env.proj),
        )
        self.assertEqual(code, 0, msg=str(payload))

    def _ok(self, *args: str) -> dict[str, Any]:
        code, payload = self.env.run_json(self.project, *args)
        self.assertEqual(code, 0, msg=str(payload))
        self.assertTrue(payload.get("ok"), msg=str(payload))
        return payload.get("data") or {}

    def _err(self, *args: str) -> dict[str, Any]:
        code, payload = self.env.run_json(self.project, *args)
        self.assertNotEqual(code, 0, msg=str(payload))
        return payload.get("error") or {}

    def _db_topic(self, topic_id: str) -> dict[str, Any]:
        conn = open_project_db(self.project, init=True)
        try:
            row = conn.execute(
                "SELECT * FROM topics WHERE id = ?", (topic_id,)
            ).fetchone()
            self.assertIsNotNone(row)
            return {k: row[k] for k in row.keys()}
        finally:
            conn.close()

    def _task_count(self) -> int:
        conn = open_project_db(self.project, init=True)
        try:
            return int(conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])
        finally:
            conn.close()

    def test_argv_agent_start_ready_enqueue_merge_without_runtime(self) -> None:
        started = self._ok(
            "topic-start",
            "auth",
            "--title",
            "Auth",
            "--goal",
            "ship",
            "--branch",
            "feat/auth",
            "--agent",
            "coder",
        )
        topic = started["topic"]
        topic_id = topic["id"]
        wt = Path(topic["worktree_path"])
        self.assertEqual(topic["lifecycle_state"], "proposed")
        self.assertEqual(topic["agent_name"], "coder")
        self.assertEqual(
            wt.resolve(),
            (self.env.proj / "worktrees" / "coder-feat__auth").resolve(),
        )
        db = self._db_topic(topic_id)
        self.assertEqual(db["agent_name"], "coder")
        self.assertEqual(db["lifecycle_state"], "proposed")

        sha = commit_file(wt, "auth.py", "x = 1\n")
        ready = self._ok(
            "topic-ready",
            topic_id,
            "--commit",
            sha,
            "--command",
            "pytest",
        )
        self.assertFalse(ready["enqueued"])
        self.assertEqual(self._task_count(), 0)
        self.assertEqual(self._db_topic(topic_id)["lifecycle_state"], "ready")

        enq = self._ok("topic-enqueue", topic_id)
        self.assertTrue(enq["enqueued"])
        self.assertEqual(self._task_count(), 1)
        self.assertEqual(self._db_topic(topic_id)["lifecycle_state"], "enqueued")
        self.assertEqual(self._db_topic(topic_id)["task_id"], enq["task_id"])

        merged = self._ok("merge", "--once")
        processed = merged["processed"]
        self.assertEqual(len(processed), 1)
        self.assertEqual(processed[0]["status"], "merged")
        self.assertEqual(self._db_topic(topic_id)["lifecycle_state"], "merged")

    def test_argv_continue_backfills_agent_so_enqueue_works(self) -> None:
        started = self._ok(
            "topic-start",
            "repair",
            "--title",
            "Repair",
            "--goal",
            "ship",
            "--branch",
            "feat/repair",
        )
        topic_id = started["topic"]["id"]
        wt = Path(started["topic"]["worktree_path"])
        self.assertIsNone(started["topic"]["agent_name"])
        err = self._err("topic-enqueue", topic_id)
        self.assertEqual(err.get("kind"), "topic_not_ready")

        sha = commit_file(wt, "r.py", "r = 1\n")
        self._ok("topic-ready", topic_id, "--commit", sha, "--command", "pytest")
        err = self._err("topic-enqueue", topic_id)
        self.assertEqual(err.get("kind"), "topic_agent_required")

        again = self._ok(
            "topic-start",
            "repair",
            "--title",
            "Repair",
            "--goal",
            "ship",
            "--branch",
            "feat/repair",
            "--agent",
            "coder",
        )
        self.assertEqual(again["topic"]["id"], topic_id)
        self.assertEqual(self._db_topic(topic_id)["agent_name"], "coder")
        self.assertEqual(
            Path(self._db_topic(topic_id)["worktree_path"]).resolve(),
            wt.resolve(),
        )
        self.assertEqual(Path(again["attach"]["directory"]).resolve(), wt.resolve())
        enq = self._ok("topic-enqueue", topic_id)
        self.assertTrue(enq["enqueued"])

    def test_argv_enqueue_rejects_commit_after_ready(self) -> None:
        started = self._ok(
            "topic-start",
            "drift",
            "--title",
            "Drift",
            "--goal",
            "ship",
            "--branch",
            "feat/drift",
            "--agent",
            "coder",
        )
        topic_id = started["topic"]["id"]
        wt = Path(started["topic"]["worktree_path"])
        sha = commit_file(wt, "a.py", "a = 1\n")
        self._ok("topic-ready", topic_id, "--commit", sha, "--command", "pytest")
        commit_file(wt, "b.py", "b = 1\n")
        err = self._err("topic-enqueue", topic_id)
        self.assertEqual(err.get("kind"), "topic_verification_sha_mismatch")
        self.assertEqual(self._task_count(), 0)
        self.assertEqual(self._db_topic(topic_id)["lifecycle_state"], "ready")

    def test_argv_reset_stuck_merged_writes_topic(self) -> None:
        started = self._ok(
            "topic-start",
            "stuck",
            "--title",
            "Stuck",
            "--goal",
            "ship",
            "--branch",
            "feat/stuck",
            "--agent",
            "coder",
        )
        topic_id = started["topic"]["id"]
        wt = Path(started["topic"]["worktree_path"])
        sha = commit_file(wt, "s.py", "s = 1\n")
        self._ok("topic-ready", topic_id, "--commit", sha, "--command", "pytest")
        enq = self._ok("topic-enqueue", topic_id)
        task_id = enq["task_id"]

        conn = open_project_db(self.project, init=True)
        try:
            conn.execute(
                "UPDATE tasks SET status = 'recovery_required' WHERE id = ?",
                (task_id,),
            )
            conn.commit()
        finally:
            conn.close()

        from tests.helpers.git_fixture import run

        main = self.env.proj / "main"
        run(["git", "merge", "--no-ff", "-m", "land stuck", sha], cwd=main)

        recovered = self._ok("reset-stuck")
        recs = recovered.get("recovered") or []
        self.assertTrue(recs, msg=str(recovered))
        self.assertEqual(recs[0]["recovered_as"], "merged")
        self.assertEqual(self._db_topic(topic_id)["lifecycle_state"], "merged")

    def _topic_through_merge(self, name: str, branch: str, filename: str) -> tuple[str, str, Path]:
        started = self._ok(
            "topic-start",
            name,
            "--title",
            name.title(),
            "--goal",
            "ship",
            "--branch",
            branch,
            "--agent",
            "coder",
        )
        topic_id = started["topic"]["id"]
        wt = Path(started["topic"]["worktree_path"])
        sha = commit_file(wt, filename, "x = 1\n")
        self._ok("topic-ready", topic_id, "--commit", sha, "--command", "pytest")
        enq = self._ok("topic-enqueue", topic_id)
        task_id = enq["task_id"]
        self._ok("merge", "--once")
        self.assertEqual(self._db_topic(topic_id)["lifecycle_state"], "merged")
        return topic_id, task_id, wt

    def test_argv_prune_archives_merged_topic(self) -> None:
        topic_id, task_id, wt = self._topic_through_merge(
            "prunable", "feat/prunable", "p.py"
        )
        _backdate_finished(self.project, task_id)
        pruned = self._ok("cleanup", "--prune")
        results = {r["task_id"]: r for r in pruned["results"]}
        self.assertTrue(results[task_id].get("ok"), results[task_id])
        self.assertFalse(wt.exists())
        row = self._db_topic(topic_id)
        self.assertEqual(row["lifecycle_state"], "archived")
        self.assertIsNotNone(row["archived_at"])
        self.assertEqual(row["last_step"], "prune")

    def test_argv_prune_refuses_live_topic(self) -> None:
        topic_id, task_id, wt = self._topic_through_merge(
            "leaky", "feat/leaky", "l.py"
        )
        conn = open_project_db(self.project, init=True)
        try:
            conn.execute(
                """
                UPDATE topics
                SET lifecycle_state = 'enqueued', archived_at = NULL
                WHERE id = ?
                """,
                (topic_id,),
            )
            conn.commit()
        finally:
            conn.close()
        _backdate_finished(self.project, task_id)
        code, payload = self.env.run_json(self.project, "cleanup", "--prune", "--json")
        self.assertEqual(code, 0, msg=str(payload))
        results = {r["task_id"]: r for r in payload["data"]["results"]}
        self.assertFalse(results[task_id].get("ok"), results[task_id])
        self.assertEqual(results[task_id].get("reason"), "topic_prune_blocked")
        self.assertEqual(results[task_id].get("kind"), "topic_prune_blocked")
        self.assertTrue(wt.exists())
        self.assertEqual(self._db_topic(topic_id)["lifecycle_state"], "enqueued")

    def test_argv_topic_ready_enqueue_by_name(self) -> None:
        started = self._ok(
            "topic-start",
            "named",
            "--title",
            "Named",
            "--goal",
            "ship",
            "--branch",
            "feat/named",
            "--agent",
            "coder",
        )
        topic_id = started["topic"]["id"]
        wt = Path(started["topic"]["worktree_path"])
        sha = commit_file(wt, "n.py", "n = 1\n")
        ready = self._ok(
            "topic-ready", "named", "--commit", sha, "--command", "pytest"
        )
        self.assertEqual(ready["topic_id"], topic_id)
        enq = self._ok("topic-enqueue", "named")
        self.assertEqual(enq["task_id"], self._db_topic(topic_id)["task_id"])
        shown = self._ok("topic-show", "named")
        self.assertEqual(shown["topic"]["id"], topic_id)

    def test_argv_topic_ref_id_wins_over_name(self) -> None:
        first = self._ok(
            "topic-start",
            "alphaone",
            "--title",
            "A",
            "--goal",
            "ship",
            "--branch",
            "feat/alphaone",
            "--agent",
            "coder",
        )
        first_id = first["topic"]["id"]
        self._ok(
            "topic-start",
            first_id,
            "--title",
            "Collision",
            "--goal",
            "ship",
            "--branch",
            "feat/collision",
            "--agent",
            "coder",
        )
        shown = self._ok("topic-show", first_id)
        self.assertEqual(shown["topic"]["id"], first_id)
        self.assertEqual(shown["topic"]["name"], "alphaone")

    def test_argv_ready_attestation_does_not_synthesize_exit_code(self) -> None:
        started = self._ok(
            "topic-start",
            "attest",
            "--title",
            "Attest",
            "--goal",
            "ship",
            "--branch",
            "feat/attest",
            "--agent",
            "coder",
        )
        topic_id = started["topic"]["id"]
        wt = Path(started["topic"]["worktree_path"])
        sha = commit_file(wt, "t.py", "t = 1\n")
        self._ok("topic-ready", topic_id, "--commit", sha, "--command", "pytest")
        conn = open_project_db(self.project, init=False)
        try:
            row = conn.execute(
                """
                SELECT results_json FROM verification_records
                WHERE topic_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (topic_id,),
            ).fetchone()
            self.assertIsNotNone(row)
            import json

            results = json.loads(row["results_json"])
            for item in results:
                self.assertNotEqual(item.get("exit_code"), 0)
                self.assertFalse(item.get("executed", True))
                self.assertEqual(item.get("trust_model"), "attestation")
        finally:
            conn.close()

    def test_argv_doctor_after_init(self) -> None:
        data = self._ok("doctor")
        self.assertEqual(data["schema"]["classify"], "v4")
        self.assertEqual(data["conflicts"], [])
        self.assertEqual(data["orphans"], [])

    def test_argv_doctor_missing_database(self) -> None:
        from orch.constants import project_db_path

        project_db_path(self.project).unlink()
        err = self._err("doctor")
        self.assertEqual(err.get("kind"), "database_not_initialized")

    def test_argv_brief_file_stores_path_and_digest(self) -> None:
        import hashlib

        brief = self.env.proj / "docs" / "plan.md"
        brief.parent.mkdir(parents=True)
        payload = b"# topic plan\n"
        brief.write_bytes(payload)
        started = self._ok(
            "topic-start",
            "briefed",
            "--title",
            "Briefed",
            "--goal",
            "ship",
            "--branch",
            "feat/briefed",
            "--agent",
            "coder",
            "--brief-file",
            "docs/plan.md",
        )
        plan_path = started["topic"]["plan_path"]
        self.assertNotIn("brief:", plan_path)
        digest = hashlib.sha256(payload).hexdigest()
        self.assertIn(digest, plan_path)
        self.assertIn("sha256:", plan_path)
        self.assertTrue(plan_path.startswith(str(brief.resolve())) or digest in plan_path)
        outsider = self.env.home / "outside.md"
        outsider.write_text("nope\n", encoding="utf-8")
        err = self._err(
            "topic-start",
            "outside",
            "--title",
            "Out",
            "--goal",
            "ship",
            "--branch",
            "feat/outside",
            "--brief-file",
            str(outsider),
        )
        self.assertEqual(err.get("kind"), "topic_brief_outside_root")


if __name__ == "__main__":
    unittest.main()
