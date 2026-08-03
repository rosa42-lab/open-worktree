"""V13-010/011：master release + release-sync + 双冻结 E2E（mock provider）。"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

from orch.config import read_config, write_config_atomic
from orch.db import open_project_db
from orch.errors import PrecheckError, ValidationError
from orch.merge.claim import claim_next
from orch.promotion.config import default_promotion_entry, validate_promotion_fields
from orch.promotion.reconcile import promotion_cancel
from orch.promotion.release_service import release_create, release_status
from orch.promotion.release_sync import release_sync
from orch.promotion.state import assert_master_transition
from orch.remote.git import CliRemoteGitAdapter
from orch.verification.service import create_aggregate
from tests.helpers.git_fixture import run
from tests.helpers.orch_env import OrchEnvTestCase


def _ensure_master(bare: Path) -> None:
    try:
        run(["git", "--git-dir", str(bare), "show-ref", "--verify", "refs/heads/master"])
    except Exception:
        run(["git", "--git-dir", str(bare), "branch", "master", "develop"])


def _set_origin(bare: Path, origin: Path) -> None:
    listed = subprocess.run(
        ["git", "--git-dir", str(bare), "remote"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    if "origin" in listed:
        run(["git", "--git-dir", str(bare), "remote", "set-url", "origin", str(origin)])
    else:
        run(["git", "--git-dir", str(bare), "remote", "add", "origin", str(origin)])


class FakeReleaseProvider:
    def __init__(self) -> None:
        self.prs: dict[str, dict[str, Any]] = {}
        self._next = 1
        self.merged = False
        self.merge_sha: str | None = None
        self.head_sha: str = ""
        self.slip_head = False

    def probe_capabilities(self) -> dict[str, Any]:
        return {"ok": True, "checks": [], "identity": {"type": "fake"}}

    def branch_policy(self, branch: str) -> dict[str, Any]:
        return {
            "exists": True,
            "allow_force": False,
            "allow_delete": False,
            "require_pr": True,
            "required_checks": ["test"],
            "bypass_summary": [],
            "merge_methods": ["merge_commit"],
            "status": "verified",
        }

    def create_promotion_pr(
        self, head: str, base: str, title: str, body: str
    ) -> dict[str, Any]:
        for pr in self.prs.values():
            if pr["head"] == head and pr["base"] == base and pr["state"] == "open":
                return dict(pr)
        eid = str(self._next)
        self._next += 1
        pr = {
            "external_id": eid,
            "url": f"https://example.test/pr/{eid}",
            "head": head,
            "base": base,
            "head_sha": self.head_sha,
            "base_sha": "base",
            "state": "open",
            "merged": False,
            "merge_commit_sha": None,
            "mergeable": True,
            "mergeable_state": "clean",
        }
        self.prs[eid] = pr
        return dict(pr)

    def get_pr(self, external_id: str) -> dict[str, Any]:
        pr = dict(self.prs[external_id])
        if self.slip_head:
            pr["head_sha"] = "slipped" + pr["head_sha"]
        if self.merged:
            pr["state"] = "closed"
            pr["merged"] = True
            pr["merge_commit_sha"] = self.merge_sha
            pr["merge_method"] = "merge_commit"
        return pr

    def get_checks(self, external_id: str, source_sha: str) -> dict[str, Any]:
        return {
            "external_id": external_id,
            "source_sha": source_sha,
            "checks": [
                {
                    "name": "test",
                    "conclusion": "success",
                    "status": "completed",
                    "head_sha": source_sha,
                    "bound_to_source": True,
                }
            ],
        }

    def get_reviews(self, external_id: str, source_sha: str) -> dict[str, Any]:
        return {
            "external_id": external_id,
            "source_sha": source_sha,
            "reviews": [
                {
                    "actor": "alice",
                    "state": "APPROVED",
                    "commit_id": source_sha,
                    "bound_to_source": True,
                    "is_bot": False,
                    "counts_as_code_owner": True,
                }
            ],
            "approved_bound_human_count": 1,
        }


class TestMasterStateMachine(unittest.TestCase):
    def test_legal_and_illegal(self) -> None:
        assert_master_transition("created", "prechecking")
        assert_master_transition("syncing", "released")
        with self.assertRaises(ValidationError):
            assert_master_transition("master_merged_pending_sync", "released")
        with self.assertRaises(ValidationError):
            assert_master_transition("syncing", "cancelled")


class TestReleaseE2E(OrchEnvTestCase):
    def _wire(self) -> None:
        self.bare = self.env.proj / ".bare.git"
        _ensure_master(self.bare)
        # advance develop ahead of master
        wt = Path(self.env.proj).parent / "wt-rel"
        if wt.exists():
            import shutil

            shutil.rmtree(wt)
        run(["git", "clone", str(self.bare), str(wt)])
        run(["git", "checkout", "develop"], cwd=wt)
        run(["git", "config", "user.email", "t@e.com"], cwd=wt)
        run(["git", "config", "user.name", "T"], cwd=wt)
        (wt / "feat.txt").write_text("feat\n", encoding="utf-8")
        run(["git", "add", "feat.txt"], cwd=wt)
        run(["git", "commit", "-m", "feat"], cwd=wt)
        run(["git", "push", str(self.bare), "develop"], cwd=wt)

        self.origin = Path(self.env.proj).parent / "origin-rel.git"
        if self.origin.exists():
            import shutil

            shutil.rmtree(self.origin)
        run(["git", "clone", "--bare", str(self.bare), str(self.origin)])
        _set_origin(self.bare, self.origin)

        self.adapter = CliRemoteGitAdapter()
        self.adapter.fetch_core_refs(self.bare, "origin", "develop", "master")
        self.source = self.adapter.local_head(self.bare, "develop")
        assert self.source

        # config: GitHub identity first, then real path origin
        _set_origin(self.bare, Path("https://github.com/acme/shop.git"))
        entry = validate_promotion_fields(
            {
                **default_promotion_entry(),
                "repository": "acme/shop",
                "required_checks": ["test"],
                "required_approvals": 1,
            }
        )
        cfg = read_config()
        cfg.setdefault("promotion", {})[self.project] = entry
        write_config_atomic(cfg)
        _set_origin(self.bare, self.origin)
        conn = open_project_db(self.project, init=True)
        try:
            rec = create_aggregate(
                conn,
                project=self.project,
                commit_sha=self.source,
                commands=["test"],
                results=[{"command": "test", "exit_code": 0, "ok": True}],
                created_by="test",
                scope="develop_publish",
            )
            conn.commit()
            self.verification_id = rec["id"]
        finally:
            conn.close()

        self.provider = FakeReleaseProvider()
        self.provider.head_sha = self.source

    def _make_merge_commit_on_origin(self) -> str:
        wt = Path(self.env.proj).parent / "merge-wt"
        if wt.exists():
            import shutil

            shutil.rmtree(wt)
        run(["git", "clone", str(self.origin), str(wt)])
        run(["git", "config", "user.email", "t@e.com"], cwd=wt)
        run(["git", "config", "user.name", "T"], cwd=wt)
        run(["git", "checkout", "master"], cwd=wt)
        run(
            ["git", "merge", "--no-ff", "origin/develop", "-m", "Merge develop"],
            cwd=wt,
        )
        run(["git", "push", "origin", "master"], cwd=wt)
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(wt),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        self.adapter.fetch_core_refs(self.bare, "origin", "develop", "master")
        # also update local bare master tip for ancestry checks
        run(
            [
                "git",
                "--git-dir",
                str(self.bare),
                "fetch",
                str(self.origin),
                "+refs/heads/master:refs/heads/master",
            ]
        )
        return sha

    def test_full_release_sync_flow(self) -> None:
        self._wire()
        dry = release_create(
            self.project,
            verification=self.verification_id,
            execute=False,
            adapter=self.adapter,
            provider=self.provider,
        )
        self.assertTrue(dry.get("ok_to_execute"), dry)

        created = release_create(
            self.project,
            verification=self.verification_id,
            execute=True,
            adapter=self.adapter,
            provider=self.provider,
        )
        self.assertTrue(created.get("ok"), created)
        pid = created["promotion"]["id"]
        self.assertEqual(created["promotion"]["state"], "awaiting_checks")

        conn = open_project_db(self.project, init=True)
        try:
            with self.assertRaises(PrecheckError):
                claim_next(conn, self.source, project_name=self.project)
        finally:
            conn.close()

        with self.assertRaises(ValidationError) as cctx:
            promotion_cancel(self.project, pid, reason="nope", fetch=False)
        self.assertEqual(cctx.exception.kind, "promotion_cancel_refused")

        st = release_status(
            self.project,
            pid,
            fetch=False,
            adapter=self.adapter,
            provider=self.provider,
        )
        self.assertIn(st["promotion"]["state"], ("awaiting_approval", "ready_to_merge"))

        merge_sha = self._make_merge_commit_on_origin()
        self.provider.merged = True
        self.provider.merge_sha = merge_sha

        st2 = release_status(
            self.project,
            pid,
            fetch=True,
            adapter=self.adapter,
            provider=self.provider,
        )
        self.assertEqual(st2["promotion"]["state"], "master_merged_pending_sync", st2)

        with self.assertRaises(ValidationError):
            promotion_cancel(self.project, pid, reason="hide", fetch=False)

        preview = release_sync(
            self.project,
            pid,
            execute=False,
            fetch=True,
            adapter=self.adapter,
            provider=self.provider,
        )
        self.assertTrue(preview.get("ok_to_execute"), preview)

        done = release_sync(
            self.project,
            pid,
            execute=True,
            fetch=True,
            adapter=self.adapter,
            provider=self.provider,
        )
        self.assertEqual(done.get("sync"), "released", done)
        self.assertEqual(done["promotion"]["state"], "released")

        tip = self.adapter._live_remote_tip(self.bare, "origin", "develop")
        self.assertEqual(tip, merge_sha)
        self.assertEqual(self.adapter.local_head(self.bare, "develop"), merge_sha)

        conn = open_project_db(self.project, init=True)
        try:
            out = claim_next(conn, merge_sha, project_name=self.project)
            self.assertIsNone(out)
        finally:
            conn.close()

    def test_head_slip_blocks(self) -> None:
        self._wire()
        created = release_create(
            self.project,
            verification=self.verification_id,
            execute=True,
            adapter=self.adapter,
            provider=self.provider,
        )
        pid = created["promotion"]["id"]
        self.provider.slip_head = True
        st = release_status(
            self.project,
            pid,
            fetch=False,
            adapter=self.adapter,
            provider=self.provider,
        )
        self.assertEqual(st["promotion"]["state"], "blocked")

    def test_cancel_from_blocked(self) -> None:
        self._wire()
        from orch.promotion import repo as promo_repo
        from orch.util import utc_now_iso

        conn = open_project_db(self.project, init=True)
        try:
            run_row = promo_repo.create_run(
                conn,
                project_name=self.project,
                kind="master_release",
                mode="promotion_pr",
                remote_name="origin",
                provider="github",
                source_ref="refs/heads/develop",
                target_ref="refs/heads/master",
                source_sha=self.source,
                target_sha_before="b" * 40,
                created_by="t",
                state="created",
            )
            conn.execute(
                "UPDATE promotion_runs SET state='blocked', updated_at=? WHERE id=?",
                (utc_now_iso(), run_row["id"]),
            )
            conn.commit()
            pid = run_row["id"]
        finally:
            conn.close()

        out = promotion_cancel(self.project, pid, reason="abort", fetch=False)
        self.assertTrue(out["cancelled"])
        self.assertEqual(out["promotion"]["state"], "cancelled")


if __name__ == "__main__":
    unittest.main()
