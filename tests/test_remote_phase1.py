"""V13-002 / V13-003 / V13-004：promotion 配置、RemoteGit、remote-status/probe。"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from orch.config import read_config, write_config_atomic
from orch.errors import ValidationError
from orch.promotion.config import (
    parse_github_repository_identity,
    validate_promotion_fields,
)
from orch.remote.git import CliRemoteGitAdapter, classify_ref_relation
from tests.helpers.git_fixture import make_bare_with_develop, run
from tests.helpers.orch_env import OrchEnvTestCase


def _ensure_master_branch(bare: Path) -> None:
    """在 bare 上从 develop 创建 master（若尚无）。"""
    try:
        run(["git", "--git-dir", str(bare), "show-ref", "--verify", "refs/heads/master"])
    except Exception:
        run(["git", "--git-dir", str(bare), "branch", "master", "develop"])


class TestPromotionConfigUnit(unittest.TestCase):
    def test_parse_github_urls(self) -> None:
        self.assertEqual(
            parse_github_repository_identity("git@github.com:acme/shop.git"),
            "acme/shop",
        )
        self.assertEqual(
            parse_github_repository_identity("https://github.com/acme/shop.git"),
            "acme/shop",
        )
        self.assertEqual(
            parse_github_repository_identity("https://github.com/acme/shop"),
            "acme/shop",
        )

    def test_rejects_secret_keys(self) -> None:
        with self.assertRaises(ValidationError) as ctx:
            validate_promotion_fields(
                {
                    "remote": "origin",
                    "provider": "github",
                    "repository": "acme/shop",
                    "token": "sekret",
                }
            )
        self.assertEqual(ctx.exception.kind, "promotion_config_invalid")

    def test_rejects_wrong_branches(self) -> None:
        with self.assertRaises(ValidationError):
            validate_promotion_fields(
                {
                    "remote": "origin",
                    "provider": "github",
                    "repository": "acme/shop",
                    "integration_branch": "main",
                    "stable_branch": "master",
                }
            )

    def test_freeze_flags_must_be_true(self) -> None:
        with self.assertRaises(ValidationError):
            validate_promotion_fields(
                {
                    "remote": "origin",
                    "provider": "github",
                    "repository": "acme/shop",
                    "freeze_local_merge_queue_during_release": False,
                }
            )

    def test_defaults_round_trip_shape(self) -> None:
        out = validate_promotion_fields(
            {
                "remote": "origin",
                "provider": "github",
                "repository": "acme/shop",
            }
        )
        self.assertEqual(out["integration_branch"], "develop")
        self.assertEqual(out["stable_branch"], "master")
        self.assertEqual(out["release_merge_method"], "merge_commit")
        self.assertTrue(out["freeze_develop_during_release"])
        self.assertTrue(out["freeze_local_merge_queue_during_release"])


class TestRemoteGitAdapter(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.proj = make_bare_with_develop(self.root / "proj")
        self.bare = self.proj / ".bare.git"
        _ensure_master_branch(self.bare)
        self.origin = self.root / "origin.git"
        run(["git", "clone", "--bare", str(self.bare), str(self.origin)])
        # bare 克隆源偶发已有 origin；幂等设置
        listed = subprocess.run(
            ["git", "--git-dir", str(self.bare), "remote"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        if "origin" in listed:
            run(
                [
                    "git",
                    "--git-dir",
                    str(self.bare),
                    "remote",
                    "set-url",
                    "origin",
                    str(self.origin),
                ]
            )
        else:
            run(
                [
                    "git",
                    "--git-dir",
                    str(self.bare),
                    "remote",
                    "add",
                    "origin",
                    str(self.origin),
                ]
            )
        self.adapter = CliRemoteGitAdapter()

    def tearDown(self) -> None:
        self._td.cleanup()

    def test_fetch_and_heads(self) -> None:
        self.adapter.fetch_core_refs(self.bare, "origin", "develop", "master")
        local = self.adapter.local_head(self.bare, "develop")
        remote = self.adapter.remote_head(self.bare, "origin", "develop")
        self.assertIsNotNone(local)
        self.assertEqual(local, remote)
        self.assertEqual(
            classify_ref_relation(self.adapter, self.bare, local, remote),
            "in_sync",
        )

    def test_local_ahead(self) -> None:
        self.adapter.fetch_core_refs(self.bare, "origin", "develop", "master")
        wt = self.root / "wt"
        run(["git", "clone", str(self.bare), str(wt)])
        run(["git", "checkout", "develop"], cwd=wt)
        run(["git", "config", "user.email", "test@example.com"], cwd=wt)
        run(["git", "config", "user.name", "Test"], cwd=wt)
        (wt / "ahead.txt").write_text("x\n", encoding="utf-8")
        run(["git", "add", "ahead.txt"], cwd=wt)
        run(["git", "commit", "-m", "ahead"], cwd=wt)
        run(["git", "push", str(self.bare), "develop"], cwd=wt)
        local = self.adapter.local_head(self.bare, "develop")
        remote = self.adapter.remote_head(self.bare, "origin", "develop")
        self.assertEqual(
            classify_ref_relation(self.adapter, self.bare, local, remote),
            "local_ahead",
        )

    def test_write_paths_cas_enabled(self) -> None:
        # sync_verified_merge rejects non-ancestor pairs
        from orch.errors import OrchError

        with self.assertRaises(OrchError) as ctx:
            self.adapter.sync_verified_merge(self.bare, "a" * 40, "b" * 40)
        self.assertEqual(ctx.exception.kind, "remote_sync_not_ff")

    def test_missing_remote_branch_unknown_relation(self) -> None:
        self.assertEqual(
            classify_ref_relation(self.adapter, self.bare, None, None),
            "unknown",
        )


class TestRemoteCommands(OrchEnvTestCase):
    def _wire_origin(self) -> None:
        bare = self.env.proj / ".bare.git"
        _ensure_master_branch(bare)
        origin = Path(self.env.proj).parent / "origin.git"
        if not origin.exists():
            run(["git", "clone", "--bare", str(bare), str(origin)])
        listed = subprocess.run(
            ["git", "--git-dir", str(bare), "remote"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        if "origin" not in listed:
            run(
                [
                    "git",
                    "--git-dir",
                    str(bare),
                    "remote",
                    "add",
                    "origin",
                    str(origin),
                ]
            )
        run(
            [
                "git",
                "--git-dir",
                str(bare),
                "remote",
                "set-url",
                "origin",
                "https://github.com/acme/shop.git",
            ]
        )
        self._origin_path = origin

    def test_remote_config_round_trip(self) -> None:
        self._wire_origin()
        code, data = self.env.run_json(
            self.project,
            "remote-config",
            "--remote",
            "origin",
            "--provider",
            "github",
            "--repository",
            "acme/shop",
        )
        self.assertEqual(code, 0, data)
        self.assertTrue(data.get("ok"))
        promo = data["data"]["promotion"]
        self.assertEqual(promo["repository"], "acme/shop")
        self.assertTrue(promo["freeze_develop_during_release"])
        self.assertFalse(data["data"]["capabilities_verified"])

    def test_remote_config_rejects_bad_repository(self) -> None:
        self._wire_origin()
        code, data = self.env.run_json(
            self.project,
            "remote-config",
            "--repository",
            "not-a-repo",
        )
        self.assertNotEqual(code, 0)
        self.assertFalse(data.get("ok", True))

    def test_remote_probe_has_five_categories(self) -> None:
        self._wire_origin()
        code, data = self.env.run_json(
            self.project,
            "remote-config",
            "--repository",
            "acme/shop",
        )
        self.assertEqual(code, 0, data)
        code, data = self.env.run_json(
            self.project,
            "remote-probe",
            "--no-fetch",
        )
        self.assertEqual(code, 0, data)
        cats = data["data"]["categories"]
        self.assertEqual(
            set(cats),
            {"git", "identity", "develop_policy", "master_policy", "provider"},
        )
        self.assertEqual(data["data"]["overall"], "unknown")
        self.assertFalse(data["data"]["overall_pass"])
        self.assertFalse(data["data"]["write_paths_enabled"])

    def test_remote_status_in_sync_after_fetch(self) -> None:
        self._wire_origin()
        bare = self.env.proj / ".bare.git"
        run(
            [
                "git",
                "--git-dir",
                str(bare),
                "remote",
                "set-url",
                "origin",
                str(self._origin_path),
            ]
        )
        cfg = read_config()
        cfg.setdefault("promotion", {})[self.project] = validate_promotion_fields(
            {
                "remote": "origin",
                "provider": "github",
                "repository": "acme/shop",
            }
        )
        write_config_atomic(cfg)

        code, data = self.env.run_json(self.project, "remote-status")
        self.assertEqual(code, 0, data)
        body = data["data"]
        self.assertTrue(body["fetched"])
        self.assertEqual(body["develop_relation"], "in_sync")
        self.assertIsNone(body["last_successful_promotion_sha"])
        self.assertFalse(body["write_performed"])
        self.assertIsNotNone(body["local_develop_sha"])
        self.assertEqual(body["local_develop_sha"], body["remote_develop_sha"])
