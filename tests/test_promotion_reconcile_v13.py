"""V13-008：promotion-list / show / reconcile / cancel。"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

from orch.config import read_config, write_config_atomic
from orch.db import open_project_db
from orch.errors import ValidationError
from orch.promotion import repo as promo_repo
from orch.promotion.config import default_promotion_entry, validate_promotion_fields
from orch.promotion.reconcile import (
    promotion_cancel,
    promotion_list,
    promotion_reconcile,
    promotion_show,
)
from orch.remote.git import CliRemoteGitAdapter
from tests.helpers.git_fixture import make_bare_with_develop, run
from tests.helpers.orch_env import OrchEnvTestCase


def _ensure_master(bare: Path) -> None:
    try:
        run(["git", "--git-dir", str(bare), "show-ref", "--verify", "refs/heads/master"])
    except Exception:
        run(["git", "--git-dir", str(bare), "branch", "master", "develop"])


def _set_origin(bare: Path, origin: Path | str) -> None:
    listed = subprocess.run(
        ["git", "--git-dir", str(bare), "remote"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    url = str(origin)
    if "origin" in listed:
        run(["git", "--git-dir", str(bare), "remote", "set-url", "origin", url])
    else:
        run(["git", "--git-dir", str(bare), "remote", "add", "origin", url])


class TestPromotionReconcileCancel(OrchEnvTestCase):
    def _wire(self) -> Path:
        bare = self.env.proj / ".bare.git"
        _ensure_master(bare)
        origin = Path(self.env.proj).parent / "origin.git"
        if not origin.exists():
            run(["git", "clone", "--bare", str(bare), str(origin)])
        _set_origin(bare, "https://github.com/acme/shop.git")
        entry = validate_promotion_fields(
            {**default_promotion_entry(), "repository": "acme/shop"}
        )
        cfg = read_config()
        cfg.setdefault("promotion", {})[self.project] = entry
        write_config_atomic(cfg)
        _set_origin(bare, origin)
        return origin

    def _seed_run(
        self,
        *,
        state: str,
        source_sha: str,
        old_sha: str,
    ) -> str:
        conn = open_project_db(self.project, init=True)
        try:
            run_row = promo_repo.create_run(
                conn,
                project_name=self.project,
                kind="develop_publish",
                mode="direct_ff",
                remote_name="origin",
                provider="github",
                source_ref="refs/heads/develop",
                target_ref="refs/heads/develop",
                source_sha=source_sha,
                target_sha_before=old_sha,
                created_by="test",
                state="created",
            )
            # 推进到目标态（简化：直接 update，绕过中间态校验用于夹具）
            if state != "created":
                # 用合法链：created→prechecking→…
                from orch.promotion.reconcile import _transition

                cur = run_row
                chain = {
                    "prechecking": ["prechecking"],
                    "ready": ["prechecking", "ready"],
                    "executing": ["prechecking", "ready", "executing"],
                    "reconciling": ["prechecking", "ready", "executing", "reconciling"],
                    "failed_safe_to_retry": [
                        "prechecking",
                        "ready",
                        "executing",
                        "reconciling",
                        "failed_safe_to_retry",
                    ],
                    "manual_required": [
                        "prechecking",
                        "ready",
                        "executing",
                        "reconciling",
                        "manual_required",
                    ],
                    "blocked": ["prechecking", "blocked"],
                }[state]
                for st in chain:
                    cur = _transition(
                        conn,
                        cur,
                        st,
                        event_type="fixture",
                        source="test",
                    )
            conn.commit()
            return run_row["id"] if state == "created" else cur["id"]
        finally:
            conn.close()

    def test_list_show_cancel(self) -> None:
        self._wire()
        adapter = CliRemoteGitAdapter()
        bare = self.env.proj / ".bare.git"
        old = adapter.local_head(bare, "develop")
        assert old
        # 造一个未推送的“假” source sha：用 old 作为双方，cancel 夹具不依赖 tip==source
        pid = self._seed_run(state="ready", source_sha="a" * 40, old_sha=old)
        listed = promotion_list(self.project)
        self.assertGreaterEqual(listed["count"], 1)
        shown = promotion_show(self.project, pid)
        self.assertEqual(shown["promotion"]["id"], pid)
        self.assertTrue(shown["events"])

        out = promotion_cancel(self.project, pid, reason="abandon fixture")
        self.assertTrue(out["cancelled"])
        self.assertEqual(out["promotion"]["state"], "cancelled")

        # 槽位释放：可再建同 kind
        conn = open_project_db(self.project, init=True)
        try:
            promo_repo.create_run(
                conn,
                project_name=self.project,
                kind="develop_publish",
                mode="direct_ff",
                remote_name="origin",
                provider="github",
                source_ref="refs/heads/develop",
                target_ref="refs/heads/develop",
                source_sha="b" * 40,
                target_sha_before=old,
                created_by="test",
            )
            conn.commit()
        finally:
            conn.close()

    def test_reconcile_old_and_new(self) -> None:
        origin = self._wire()
        adapter = CliRemoteGitAdapter()
        bare = self.env.proj / ".bare.git"
        old = adapter._live_remote_tip(bare, "origin", "develop")
        assert old

        # local ahead commit
        main = self.env.proj / "main"
        run(["git", "config", "user.email", "t@e.com"], cwd=main)
        run(["git", "config", "user.name", "T"], cwd=main)
        (main / "r.txt").write_text("r\n", encoding="utf-8")
        run(["git", "add", "r.txt"], cwd=main)
        run(["git", "commit", "-m", "r"], cwd=main)
        new = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(main),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        pid = self._seed_run(state="reconciling", source_sha=new, old_sha=old)
        # tip still old → failed_safe_to_retry
        out = promotion_reconcile(self.project, pid)
        self.assertEqual(out["reconcile"], "failed_safe_to_retry")
        self.assertEqual(out["promotion"]["state"], "failed_safe_to_retry")

        # push new to origin, reconcile → succeeded
        adapter.push_fast_forward(
            bare,
            "origin",
            "refs/heads/develop",
            "refs/heads/develop",
            old,
            new,
        )
        out2 = promotion_reconcile(self.project, pid)
        self.assertEqual(out2["reconcile"], "succeeded")
        self.assertEqual(out2["promotion"]["state"], "succeeded")
        self.assertEqual(out2["promotion"]["published_sha"], new)

    def test_cancel_refuses_when_remote_already_published(self) -> None:
        self._wire()
        adapter = CliRemoteGitAdapter()
        bare = self.env.proj / ".bare.git"
        tip = adapter._live_remote_tip(bare, "origin", "develop")
        assert tip
        pid = self._seed_run(state="manual_required", source_sha=tip, old_sha="c" * 40)
        with self.assertRaises(ValidationError) as ctx:
            promotion_cancel(self.project, pid, reason="hide write")
        self.assertEqual(ctx.exception.kind, "promotion_cancel_refused")


if __name__ == "__main__":
    unittest.main()
