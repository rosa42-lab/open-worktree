"""V13-007：push_fast_forward CAS + promote-develop dry-run/execute。"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from orch.config import read_config, write_config_atomic
from orch.db import open_project_db
from orch.errors import OrchError
from orch.promotion.config import default_promotion_entry, validate_promotion_fields
from orch.promotion.service import promote_develop
from orch.remote.git import CliRemoteGitAdapter
from orch.verification.service import create_aggregate
from tests.helpers.git_fixture import make_bare_with_develop, run
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


class TestPushFastForward(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.proj = make_bare_with_develop(self.root / "proj")
        self.bare = self.proj / ".bare.git"
        _ensure_master(self.bare)
        self.origin = self.root / "origin.git"
        run(["git", "clone", "--bare", str(self.bare), str(self.origin)])
        _set_origin(self.bare, self.origin)
        self.adapter = CliRemoteGitAdapter()
        self.adapter.fetch_core_refs(self.bare, "origin", "develop", "master")

    def tearDown(self) -> None:
        self._td.cleanup()

    def _commit_on_bare_develop(self, name: str) -> str:
        old = self.adapter.local_head(self.bare, "develop")
        assert old
        wt = self.root / f"wt-{name}"
        run(["git", "clone", str(self.bare), str(wt)])
        run(["git", "checkout", "develop"], cwd=wt)
        run(["git", "config", "user.email", "t@e.com"], cwd=wt)
        run(["git", "config", "user.name", "T"], cwd=wt)
        (wt / f"{name}.txt").write_text(f"{name}\n", encoding="utf-8")
        run(["git", "add", f"{name}.txt"], cwd=wt)
        run(["git", "commit", "-m", name], cwd=wt)
        run(["git", "push", str(self.bare), "develop"], cwd=wt)
        new = self.adapter.local_head(self.bare, "develop")
        assert new and new != old
        return new

    def test_cas_push_succeeds(self) -> None:
        old = self.adapter.remote_head(self.bare, "origin", "develop")
        assert old
        new = self._commit_on_bare_develop("ahead")
        self.adapter.push_fast_forward(
            self.bare,
            "origin",
            "refs/heads/develop",
            "refs/heads/develop",
            old,
            new,
        )
        tip = self.adapter._live_remote_tip(self.bare, "origin", "develop")
        self.assertEqual(tip, new)

    def test_cas_race_when_tip_moved(self) -> None:
        old = self.adapter.remote_head(self.bare, "origin", "develop")
        assert old
        new = self._commit_on_bare_develop("local-ahead")
        # 第三方把 origin 推到与 new 无关的另一条提交：从 old 另开提交
        other_wt = self.root / "other"
        run(["git", "clone", str(self.origin), str(other_wt)])
        run(["git", "checkout", "develop"], cwd=other_wt)
        run(["git", "config", "user.email", "t@e.com"], cwd=other_wt)
        run(["git", "config", "user.name", "T"], cwd=other_wt)
        (other_wt / "other.txt").write_text("other\n", encoding="utf-8")
        run(["git", "add", "other.txt"], cwd=other_wt)
        run(["git", "commit", "-m", "other"], cwd=other_wt)
        run(["git", "push", "origin", "develop"], cwd=other_wt)
        # fetch 使 remote-tracking 更新；CAS 用 ls-remote/remote_head
        self.adapter.fetch_core_refs(self.bare, "origin", "develop", "master")
        with self.assertRaises(OrchError) as ctx:
            self.adapter.push_fast_forward(
                self.bare,
                "origin",
                "refs/heads/develop",
                "refs/heads/develop",
                old,
                new,
            )
        self.assertEqual(ctx.exception.kind, "remote_cas_race")

    def test_idempotent_when_already_at_new(self) -> None:
        old = self.adapter.remote_head(self.bare, "origin", "develop")
        assert old
        new = self._commit_on_bare_develop("idem")
        self.adapter.push_fast_forward(
            self.bare,
            "origin",
            "refs/heads/develop",
            "refs/heads/develop",
            old,
            new,
        )
        # 再次 push：tip==new → 直接返回
        self.adapter.push_fast_forward(
            self.bare,
            "origin",
            "refs/heads/develop",
            "refs/heads/develop",
            old,
            new,
        )
        self.assertEqual(
            self.adapter._live_remote_tip(self.bare, "origin", "develop"),
            new,
        )

    def test_sync_verified_merge_ff(self) -> None:
        old = self.adapter.local_head(self.bare, "develop")
        assert old
        # create child commit on develop
        new = self._commit_on_bare_develop("syncchild")
        # reset local develop back to old to simulate pre-sync
        run(
            ["git", "--git-dir", str(self.bare), "update-ref", "refs/heads/develop", old]
        )
        self.adapter.sync_verified_merge(self.bare, old, new)
        self.assertEqual(self.adapter.local_head(self.bare, "develop"), new)


class TestPromoteDevelop(OrchEnvTestCase):
    def _wire(self) -> Path:
        bare = self.env.proj / ".bare.git"
        _ensure_master(bare)
        origin = Path(self.env.proj).parent / "origin.git"
        if not origin.exists():
            run(["git", "clone", "--bare", str(bare), str(origin)])
        # 先用 GitHub URL 写 config（校验 identity）
        _set_origin(bare, Path("https://github.com/acme/shop.git"))
        entry = validate_promotion_fields(
            {
                **default_promotion_entry(),
                "repository": "acme/shop",
                "required_checks": ["test"],
            }
        )
        cfg = read_config()
        cfg.setdefault("promotion", {})[self.project] = entry
        write_config_atomic(cfg)
        # 再指回真实 path origin 供 push
        _set_origin(bare, origin)
        return origin

    def _ahead_local_develop(self) -> str:
        bare = self.env.proj / ".bare.git"
        main = self.env.proj / "main"
        run(["git", "config", "user.email", "t@e.com"], cwd=main)
        run(["git", "config", "user.name", "T"], cwd=main)
        (main / "promo.txt").write_text("promo\n", encoding="utf-8")
        run(["git", "add", "promo.txt"], cwd=main)
        run(["git", "commit", "-m", "promo"], cwd=main)
        # 同步到 bare develop（main 通常已连 bare）
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(main),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        return sha

    def test_dry_run_and_execute(self) -> None:
        self._wire()
        source_sha = self._ahead_local_develop()
        # origin 仍落后
        conn = open_project_db(self.project, init=True)
        try:
            rec = create_aggregate(
                conn,
                project=self.project,
                commit_sha=source_sha,
                commands=["test"],
                results=[{"command": "test", "exit_code": 0, "ok": True}],
                created_by="test",
            )
            conn.commit()
            vid = rec["id"]
        finally:
            conn.close()

        dry = promote_develop(self.project, execute=False, verification_record_id=vid)
        self.assertTrue(dry.get("ok_to_execute"))
        self.assertFalse(dry.get("write_performed"))
        self.assertEqual(dry["plan"]["source_sha"], source_sha)

        out = promote_develop(self.project, execute=True, verification_record_id=vid)
        self.assertTrue(out.get("write_performed"))
        self.assertEqual(out["promotion"]["state"], "succeeded")
        self.assertEqual(out["promotion"]["published_sha"], source_sha)

        # idempotent
        again = promote_develop(self.project, execute=True, verification_record_id=vid)
        self.assertTrue(again.get("idempotent") or again["promotion"]["state"] == "succeeded")

    def test_execute_requires_verification(self) -> None:
        self._wire()
        with self.assertRaises(Exception) as ctx:
            promote_develop(self.project, execute=True, verification_record_id=None)
        self.assertIn("verification", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
