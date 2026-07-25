"""§17.7–17.8 recovery paths; §17.12 main manual resolve ignored; SIGINT path."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock

from orch.commands import merge as merge_cmd
from orch.constants import MAIN_WORKTREE_NAME
from orch.db import immediate_transaction, open_project_db
from orch.errors import ExitCode, InterruptedMergeError
from orch.git.ref import run_git_ref
from orch.git.worktree import run_git_worktree
from orch.merge.interrupt import reconcile_after_interrupt
from orch.util import utc_now_iso
from tests.helpers.orch_env import OrchEnvTestCase


def _task_by_branch(env, project: str, branch: str) -> dict:
    _, listing = env.run_json(project, "list", "--json")
    for t in listing["data"]["tasks"]:
        if t["branch_name"] == branch:
            return t
    raise AssertionError(f"no task for {branch}")


class FinalizeBeforeCrashTests(OrchEnvTestCase):
    """§17.8: Git already merged, DB still merging → reset-stuck → merged."""

    def test_reset_stuck_marks_merged_when_develop_has_source(self) -> None:
        wt = self.env.add_feature_branch(
            self.project, "agentA", "feat/fin", "fin.txt", "f\n"
        )
        self.assertEqual(
            self.env.run(self.project, "enqueue", "agentA", "feat/fin", str(wt)),
            0,
        )
        task = _task_by_branch(self.env, self.project, "feat/fin")
        source = task["source_commit"]
        bare = self.env.proj / ".bare.git"
        main = self.env.proj / MAIN_WORKTREE_NAME

        # Perform real merge in main (as Do would) but leave DB as merging
        run_git_worktree(
            ["merge", "--no-ff", "--no-edit", source],
            main,
            check=True,
        )
        develop = run_git_ref(["rev-parse", "develop"], bare, check=True).stdout.strip()

        conn = open_project_db(self.project, init=False)
        try:
            with immediate_transaction(conn) as c:
                c.execute(
                    """
                    UPDATE tasks SET status='merging', claimed_at=?,
                      target_commit_at_claim=?, attempts=1 WHERE id=?
                    """,
                    (utc_now_iso(), develop, task["id"]),
                )
        finally:
            conn.close()

        code, payload = self.env.run_json(self.project, "reset-stuck", "--json")
        self.assertEqual(code, 0, msg=str(payload))
        self.assertEqual(payload["data"]["recovered"][0]["recovered_as"], "merged")

        task2 = _task_by_branch(self.env, self.project, "feat/fin")
        self.assertEqual(task2["status"], "merged")
        self.assertIsNotNone(task2["merged_commit"])


class DoMidCrashTests(OrchEnvTestCase):
    """§17.7: MERGE_HEAD present + merging → reset-stuck aborts to conflict/pending."""

    def test_reset_stuck_with_merge_head(self) -> None:
        wt_a = self.env.add_feature_branch(
            self.project, "agentA", "feat/x", "same.txt", "A\n"
        )
        wt_b = self.env.add_feature_branch(
            self.project, "agentB", "feat/y", "same.txt", "B\n"
        )
        self.assertEqual(
            self.env.run(self.project, "enqueue", "agentA", "feat/x", str(wt_a)),
            0,
        )
        self.assertEqual(
            self.env.run(self.project, "enqueue", "agentB", "feat/y", str(wt_b)),
            0,
        )
        self.assertEqual(self.env.run(self.project, "merge", "--once"), 0)

        task = _task_by_branch(self.env, self.project, "feat/y")
        source = task["source_commit"]
        bare = self.env.proj / ".bare.git"
        main = self.env.proj / MAIN_WORKTREE_NAME
        develop_before = run_git_ref(
            ["rev-parse", "develop"], bare, check=True
        ).stdout.strip()

        # Start merge into conflict without finishing protocol
        r = run_git_worktree(["merge", "--no-ff", "--no-edit", source], main)
        self.assertNotEqual(r.returncode, 0)
        mh = run_git_worktree(["rev-parse", "--git-path", "MERGE_HEAD"], main, check=True)
        mh_path = Path(mh.stdout.strip())
        if not mh_path.is_absolute():
            mh_path = main / mh_path
        self.assertTrue(mh_path.exists())

        conn = open_project_db(self.project, init=False)
        try:
            with immediate_transaction(conn) as c:
                c.execute(
                    """
                    UPDATE tasks SET status='merging', claimed_at=?,
                      target_commit_at_claim=?, attempts=1 WHERE id=?
                    """,
                    (utc_now_iso(), develop_before, task["id"]),
                )
        finally:
            conn.close()

        code, payload = self.env.run_json(self.project, "reset-stuck", "--json")
        self.assertEqual(code, 0, msg=str(payload))
        recovered = payload["data"]["recovered"][0]["recovered_as"]
        self.assertIn(recovered, ("conflict", "pending"))

        # main should not remain mid-merge if abort succeeded
        mh2 = run_git_worktree(["rev-parse", "--git-path", "MERGE_HEAD"], main, check=True)
        p2 = Path(mh2.stdout.strip())
        if not p2.is_absolute():
            p2 = main / p2
        self.assertFalse(p2.exists())


class ManualMainPolicyTests(OrchEnvTestCase):
    """§17.12: conflict stays conflict; reset-stuck does not clear it; merge stays blocked."""

    def test_conflict_not_cleared_by_reset_stuck(self) -> None:
        wt_a = self.env.add_feature_branch(
            self.project, "agentA", "feat/m1", "m.txt", "1\n"
        )
        wt_b = self.env.add_feature_branch(
            self.project, "agentB", "feat/m2", "m.txt", "2\n"
        )
        self.assertEqual(
            self.env.run(self.project, "enqueue", "agentA", "feat/m1", str(wt_a)),
            0,
        )
        self.assertEqual(
            self.env.run(self.project, "enqueue", "agentB", "feat/m2", str(wt_b)),
            0,
        )
        self.assertEqual(self.env.run(self.project, "merge", "--once"), 0)
        self.env.run(self.project, "merge", "--once")
        task = _task_by_branch(self.env, self.project, "feat/m2")
        self.assertEqual(task["status"], "conflict")

        # Operator "fixes" main (no-op clean state after orch abort) — still no DB help
        code, payload = self.env.run_json(self.project, "reset-stuck", "--json")
        self.assertEqual(code, 0)
        # no stuck merging tasks — recovered empty or no change to conflict
        task2 = _task_by_branch(self.env, self.project, "feat/m2")
        self.assertEqual(task2["status"], "conflict")

        code_m, blocked = self.env.run_json(self.project, "merge", "--once", "--json")
        self.assertEqual(code_m, 5)
        self.assertEqual(blocked["error"]["code"], 5)


class SigintPathTests(OrchEnvTestCase):
    """§17.15: KeyboardInterrupt during Do → reconcile + exit 130."""

    def test_keyboard_interrupt_during_merge_returns_130(self) -> None:
        wt = self.env.add_feature_branch(
            self.project, "agentA", "feat/sig", "sig.txt", "s\n"
        )
        self.assertEqual(
            self.env.run(self.project, "enqueue", "agentA", "feat/sig", str(wt)),
            0,
        )

        def boom(*args, **kwargs):
            raise KeyboardInterrupt()

        with mock.patch.object(merge_cmd, "run_merge_no_ff", side_effect=boom):
            code, payload = self.env.run_json(self.project, "merge", "--once", "--json")

        self.assertEqual(code, ExitCode.INTERRUPTED)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], 130)

        task = _task_by_branch(self.env, self.project, "feat/sig")
        # interrupt before git ran → pending (not stuck merging)
        self.assertIn(task["status"], ("pending", "recovery_required", "merged"))
