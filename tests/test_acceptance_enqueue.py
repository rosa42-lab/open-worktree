"""§17.2 / §17.17 enqueue validation."""

from __future__ import annotations

from tests.helpers.git_fixture import run
from tests.helpers.orch_env import OrchEnvTestCase


class EnqueueAcceptanceTests(OrchEnvTestCase):
    def test_empty_change_rejected(self) -> None:
        # worktree on develop tip without unique commits
        code = self.env.run(
            self.project, "worktree-add", "agentA", "feat/empty"
        )
        self.assertEqual(code, 0)
        wt = self.env.proj / "worktrees" / "agentA-feat__empty"
        # no new commit — same as develop
        code, payload = self.env.run_json(
            self.project,
            "enqueue",
            "agentA",
            "feat/empty",
            str(wt),
            "--json",
        )
        self.assertEqual(code, 7)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["kind"], "enqueue_validation_failed")

    def test_duplicate_active_branch_rejected(self) -> None:
        wt = self.env.add_feature_branch(
            self.project, "agentA", "feat/dup", "a.txt", "1\n"
        )
        self.assertEqual(
            self.env.run(
                self.project, "enqueue", "agentA", "feat/dup", str(wt)
            ),
            0,
        )
        code, payload = self.env.run_json(
            self.project,
            "enqueue",
            "agentA",
            "feat/dup",
            str(wt),
            "--json",
        )
        self.assertEqual(code, 7)
        self.assertFalse(payload["ok"])

    def test_dirty_worktree_rejected(self) -> None:
        wt = self.env.add_feature_branch(
            self.project, "agentA", "feat/dirty", "d.txt", "1\n"
        )
        (wt / "extra.txt").write_text("dirty\n", encoding="utf-8")
        code, payload = self.env.run_json(
            self.project,
            "enqueue",
            "agentA",
            "feat/dirty",
            str(wt),
            "--json",
        )
        self.assertEqual(code, 7)
        self.assertIn("clean", payload["error"]["message"].lower())
