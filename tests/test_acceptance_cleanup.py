"""§17.9 cleanup --prune safety."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from orch.db import immediate_transaction, open_project_db
from unittest import mock

from tests.helpers.git_fixture import run
from tests.helpers.orch_env import OrchEnvTestCase


def _backdate_finished(project: str, task_id: str, hours: int = 48) -> None:
    past = (
        datetime.now(timezone.utc) - timedelta(hours=hours)
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    conn = open_project_db(project, init=False)
    try:
        with immediate_transaction(conn) as c:
            c.execute(
                "UPDATE tasks SET finished_at = ? WHERE id = ?",
                (past, task_id),
            )
    finally:
        conn.close()


class CleanupPruneTests(OrchEnvTestCase):
    def test_prune_archives_safe_worktree_only(self) -> None:
        # T1: safe merged worktree
        wt1 = self.env.add_feature_branch(
            self.project, "agentA", "feat/clean1", "p1.txt", "1\n"
        )
        self.assertEqual(
            self.env.run(
                self.project, "enqueue", "agentA", "feat/clean1", str(wt1)
            ),
            0,
        )
        self.assertEqual(self.env.run(self.project, "merge", "--once"), 0)

        # T2: merge then check out same branch in a second worktree (blocks prune)
        wt2 = self.env.add_feature_branch(
            self.project, "agentB", "feat/clean2", "p2.txt", "2\n"
        )
        self.assertEqual(
            self.env.run(
                self.project, "enqueue", "agentB", "feat/clean2", str(wt2)
            ),
            0,
        )
        self.assertEqual(self.env.run(self.project, "merge", "--once"), 0)

        # T2: lock git worktree so prune must refuse (Git cannot double-checkout same branch)
        bare = self.env.proj / ".bare.git"
        run(
            [
                "git",
                "--git-dir",
                str(bare),
                "worktree",
                "lock",
                str(wt2),
            ]
        )

        _, listing = self.env.run_json(self.project, "list", "--json")
        by_branch = {t["branch_name"]: t for t in listing["data"]["tasks"]}
        t1 = by_branch["feat/clean1"]
        t2 = by_branch["feat/clean2"]
        self.assertEqual(t1["status"], "merged")
        self.assertEqual(t2["status"], "merged")

        _backdate_finished(self.project, t1["id"])
        _backdate_finished(self.project, t2["id"])

        code, payload = self.env.run_json(
            self.project, "cleanup", "--prune", "--json"
        )
        self.assertEqual(code, 0, msg=str(payload))
        results = {r["task_id"]: r for r in payload["data"]["results"]}

        self.assertTrue(results[t1["id"]].get("ok"), results[t1["id"]])
        self.assertFalse(wt1.exists(), "T1 worktree should be removed")

        self.assertFalse(results[t2["id"]].get("ok"), results[t2["id"]])
        self.assertTrue(wt2.exists(), "T2 worktree retained when locked")
        reason = (results[t2["id"]].get("reason") or "").lower()
        self.assertTrue(
            "lock" in reason,
            msg=results[t2["id"]],
        )

        _, listing2 = self.env.run_json(self.project, "list", "--all", "--json")
        tasks = {t["id"]: t for t in listing2["data"]["tasks"]}
        self.assertIsNotNone(tasks[t1["id"]].get("archived_at"))
        self.assertIsNone(tasks[t2["id"]].get("archived_at"))
        # DB rows retained
        self.assertEqual(tasks[t1["id"]]["status"], "merged")
        self.assertEqual(tasks[t2["id"]]["status"], "merged")

    def test_cleanup_list_no_prune_without_flag(self) -> None:
        wt = self.env.add_feature_branch(
            self.project, "agentA", "feat/list", "l.txt", "l\n"
        )
        self.assertEqual(
            self.env.run(self.project, "enqueue", "agentA", "feat/list", str(wt)),
            0,
        )
        self.assertEqual(self.env.run(self.project, "merge", "--once"), 0)
        code, payload = self.env.run_json(self.project, "cleanup", "--json")
        self.assertEqual(code, 0)
        self.assertFalse(payload["data"]["prune"])
        self.assertTrue(wt.exists())

    def test_prune_refuses_branch_checked_out_elsewhere(self) -> None:
        """§17.9 branch referenced by another worktree (simulated via list)."""
        from orch.commands import cleanup as cleanup_mod

        wt = self.env.add_feature_branch(
            self.project, "agentA", "feat/shared", "sh.txt", "s\n"
        )
        self.assertEqual(
            self.env.run(
                self.project, "enqueue", "agentA", "feat/shared", str(wt)
            ),
            0,
        )
        self.assertEqual(self.env.run(self.project, "merge", "--once"), 0)
        _, listing = self.env.run_json(self.project, "list", "--json")
        tid = listing["data"]["tasks"][0]["id"]
        _backdate_finished(self.project, tid)

        real_list = cleanup_mod.worktree_list_porcelain

        def fake_list(bare):
            entries = real_list(bare)
            # inject a second worktree claiming the same branch
            entries.append(
                {
                    "worktree": str(self.env.proj / "worktrees" / "other-shared"),
                    "branch": "refs/heads/feat/shared",
                    "HEAD": "deadbeef",
                }
            )
            return entries

        with mock.patch.object(cleanup_mod, "worktree_list_porcelain", side_effect=fake_list):
            code, payload = self.env.run_json(
                self.project, "cleanup", "--prune", "--json"
            )
        self.assertEqual(code, 0, msg=str(payload))
        r0 = payload["data"]["results"][0]
        self.assertFalse(r0.get("ok"))
        self.assertIn("another worktree", r0.get("reason", "").lower())
        self.assertTrue(wt.exists())
