"""Unit-level interrupt reconcilation paths."""

from __future__ import annotations

from orch.constants import project_db_path
from orch.db import immediate_transaction, open_project_db
from orch.merge.interrupt import reconcile_after_interrupt
from orch.util import utc_now_iso
from tests.helpers.orch_env import OrchEnvTestCase


class InterruptReconcileTests(OrchEnvTestCase):
    def test_interrupt_before_do_returns_pending(self) -> None:
        wt = self.env.add_feature_branch(
            self.project, "agentA", "feat/int", "i.txt", "i\n"
        )
        self.assertEqual(
            self.env.run(self.project, "enqueue", "agentA", "feat/int", str(wt)),
            0,
        )
        _, pending = self.env.run_json(self.project, "pending", "--json")
        task = pending["data"]["tasks"][0]
        from orch.git.ref import run_git_ref

        bare = self.env.proj / ".bare.git"
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
            row = conn.execute(
                "SELECT * FROM tasks WHERE id=?", (task["id"],)
            ).fetchone()
            task_dict = {k: row[k] for k in row.keys()}
            root = self.env.proj
            result = reconcile_after_interrupt(conn, root, bare, task_dict)
            self.assertEqual(result.get("recovered_as"), "pending")
        finally:
            conn.close()
