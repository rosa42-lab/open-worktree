"""§17.3 frozen SHA; §17.4 conflict blocks queue; §17.5/16 retry; §17.18 skip."""

from __future__ import annotations

import subprocess
from pathlib import Path

from tests.helpers.orch_env import OrchEnvTestCase


def _run(
    cmd: list[str],
    cwd: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=check)


def _rev_parse(cwd: Path, ref: str = "HEAD") -> str:
    return _run(["git", "rev-parse", ref], cwd).stdout.strip()


class FreezeSourceTests(OrchEnvTestCase):
    def test_diff_uses_frozen_source_after_new_commit(self) -> None:
        wt = self.env.add_feature_branch(
            self.project, "agentA", "feat/freeze", "f.txt", "v1\n"
        )
        self.assertEqual(
            self.env.run(self.project, "enqueue", "agentA", "feat/freeze", str(wt)),
            0,
        )
        _, pending = self.env.run_json(self.project, "pending", "--json")
        task = pending["data"]["tasks"][0]
        frozen = task["source_commit"]
        task_id = task["id"]

        (wt / "f.txt").write_text("v2\n", encoding="utf-8")
        _run(["git", "add", "f.txt"], cwd=wt)
        _run(["git", "commit", "-m", "v2"], cwd=wt)
        new_head = _rev_parse(wt)
        self.assertNotEqual(new_head, frozen)

        code, diff_payload = self.env.run_json(self.project, "diff", task_id, "--json")
        self.assertEqual(code, 0)
        self.assertEqual(diff_payload["data"]["source_commit"], frozen)


class ConflictAndRetryTests(OrchEnvTestCase):
    def test_conflict_blocks_then_retry_and_merge(self) -> None:
        wt_a = self.env.add_feature_branch(
            self.project, "agentA", "feat/a", "same.txt", "from-a\n"
        )
        wt_b = self.env.add_feature_branch(
            self.project, "agentB", "feat/b", "same.txt", "from-b\n"
        )
        self.assertEqual(
            self.env.run(self.project, "enqueue", "agentA", "feat/a", str(wt_a)),
            0,
        )
        self.assertEqual(
            self.env.run(self.project, "enqueue", "agentB", "feat/b", str(wt_b)),
            0,
        )

        self.assertEqual(self.env.run(self.project, "merge", "--once"), 0)

        # second merge hits conflict
        self.env.run(self.project, "merge", "--once")
        _, listing = self.env.run_json(self.project, "list", "--json")
        tasks = {t["branch_name"]: t for t in listing["data"]["tasks"]}
        self.assertEqual(tasks["feat/a"]["status"], "merged")
        self.assertEqual(tasks["feat/b"]["status"], "conflict")
        conflict_id = tasks["feat/b"]["id"]
        old_source = tasks["feat/b"]["source_commit"]

        code_block, blocked = self.env.run_json(self.project, "merge", "--once", "--json")
        self.assertEqual(code_block, 5)
        self.assertEqual(blocked["error"]["code"], 5)

        # §17.16: retry without new commit fails
        code_retry, _ = self.env.run_json(self.project, "retry", conflict_id, "--json")
        self.assertEqual(code_retry, 7)

        # Agent merges current develop into worktree and resolves
        bare = self.env.proj / ".bare.git"
        develop = _run(["git", "--git-dir", str(bare), "rev-parse", "develop"]).stdout.strip()
        mr = _run(["git", "merge", "--no-edit", develop], cwd=wt_b, check=False)
        if mr.returncode != 0:
            (wt_b / "same.txt").write_text("resolved-b\n", encoding="utf-8")
            _run(["git", "add", "same.txt"], cwd=wt_b)
            # complete merge commit if in merge state
            _run(["git", "commit", "--no-edit", "-m", "resolve conflict with develop"], cwd=wt_b, check=False)
            if _run(["git", "status", "--porcelain"], cwd=wt_b).stdout.strip():
                _run(["git", "add", "-A"], cwd=wt_b)
                _run(["git", "commit", "-m", "resolve"], cwd=wt_b, check=False)

        # ensure clean worktree
        porcelain = _run(["git", "status", "--porcelain"], cwd=wt_b).stdout
        self.assertEqual(porcelain.strip(), "", msg=porcelain)

        new_head = _rev_parse(wt_b)
        self.assertNotEqual(new_head, old_source)

        code_ok, retry_ok = self.env.run_json(self.project, "retry", conflict_id, "--json")
        self.assertEqual(code_ok, 0, msg=str(retry_ok))
        self.assertEqual(retry_ok["data"]["status"], "pending")
        self.assertEqual(retry_ok["data"]["new_source_commit"], new_head)

        self.assertEqual(self.env.run(self.project, "merge", "--once"), 0)
        _, listing2 = self.env.run_json(self.project, "list", "--json")
        tasks2 = {t["id"]: t for t in listing2["data"]["tasks"]}
        self.assertEqual(tasks2[conflict_id]["status"], "merged")


class SkipTests(OrchEnvTestCase):
    def test_skip_pending(self) -> None:
        wt = self.env.add_feature_branch(
            self.project, "agentA", "feat/skip", "s.txt", "1\n"
        )
        self.assertEqual(
            self.env.run(self.project, "enqueue", "agentA", "feat/skip", str(wt)),
            0,
        )
        _, pending = self.env.run_json(self.project, "pending", "--json")
        tid = pending["data"]["tasks"][0]["id"]
        code, payload = self.env.run_json(
            self.project, "skip", tid, "--reason", "nope", "--json"
        )
        self.assertEqual(code, 0)
        self.assertEqual(payload["data"]["status"], "skipped")

        code2, m = self.env.run_json(self.project, "merge", "--once", "--json")
        self.assertEqual(code2, 0)
        self.assertEqual(m["data"].get("message"), "no pending tasks")
