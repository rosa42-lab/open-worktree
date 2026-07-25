"""§17.6 Claim-after-crash style: merging with clean main -> pending via reset-stuck."""

from __future__ import annotations

from orch.constants import project_db_path
from orch.db import connect, immediate_transaction, open_project_db
from orch.util import utc_now_iso
from tests.helpers.orch_env import OrchEnvTestCase


class ResetStuckTests(OrchEnvTestCase):
    def test_merging_with_clean_main_resets_to_pending(self) -> None:
        wt = self.env.add_feature_branch(
            self.project, "agentA", "feat/stuck", "z.txt", "z\n"
        )
        self.assertEqual(
            self.env.run(self.project, "enqueue", "agentA", "feat/stuck", str(wt)),
            0,
        )
        _, pending = self.env.run_json(self.project, "pending", "--json")
        task = pending["data"]["tasks"][0]
        tid = task["id"]

        # Simulate claim without Do: status=merging, target_commit_at_claim=current develop
        from orch.git.ref import run_git_ref

        bare = self.env.proj / ".bare.git"
        develop = run_git_ref(["rev-parse", "develop"], bare, check=True).stdout.strip()

        conn = open_project_db(self.project, init=False)
        try:
            with immediate_transaction(conn) as c:
                c.execute(
                    """
                    UPDATE tasks SET
                      status = 'merging',
                      claimed_at = ?,
                      target_commit_at_claim = ?,
                      attempts = attempts + 1
                    WHERE id = ?
                    """,
                    (utc_now_iso(), develop, tid),
                )
        finally:
            conn.close()

        code, payload = self.env.run_json(self.project, "reset-stuck", "--json")
        self.assertEqual(code, 0, msg=str(payload))
        recovered = payload["data"]["recovered"]
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0]["recovered_as"], "pending")

        _, listing = self.env.run_json(self.project, "list", "--json")
        statuses = {t["id"]: t["status"] for t in listing["data"]["tasks"]}
        self.assertEqual(statuses[tid], "pending")

        # can still merge
        self.assertEqual(self.env.run(self.project, "merge", "--once"), 0)
