"""V12-004 runtime registry tests (no live OpenCode required)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from orch.runtime import registry as R
from orch.runtime.registry import (
    build_registry_record,
    identity_matches,
    load_credentials,
    load_registry,
    public_registry_view,
    save_credentials,
    save_registry,
)


class RegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.rt = self.home / ".orchestrator" / "runtime"
        self.patches = [
            mock.patch("orch.runtime.registry.runtime_dir", return_value=self.rt),
            mock.patch(
                "orch.runtime.registry.runtime_registry_path",
                return_value=self.rt / "opencode.json",
            ),
            mock.patch(
                "orch.runtime.registry.runtime_credentials_path",
                return_value=self.rt / "opencode.credentials.json",
            ),
            mock.patch(
                "orch.runtime.registry.runtime_lock_path",
                return_value=self.rt / "opencode.lock",
            ),
            mock.patch(
                "orch.runtime.registry.runtime_log_dir",
                return_value=self.rt / "logs",
            ),
            mock.patch(
                "orch.runtime.registry.runtime_log_path",
                return_value=self.rt / "logs" / "opencode.log",
            ),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self) -> None:
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_save_load_and_public_view_strips_secrets(self) -> None:
        rec = build_registry_record(
            server_id="srv_1",
            base_url="http://127.0.0.1:4096",
            pid=123,
            server_generation=1,
            server_nonce="nonce",
            managed_by_orch=True,
        )
        save_registry(rec)
        save_credentials(username="opencode", password="s3cret", server_id="srv_1")
        loaded = load_registry()
        assert loaded is not None
        self.assertEqual(loaded["server_id"], "srv_1")
        view = public_registry_view(loaded)
        assert view is not None
        self.assertNotIn("password", view)
        creds = load_credentials()
        assert creds is not None
        self.assertEqual(creds["password"], "s3cret")
        # credentials file mode best-effort
        text = (self.rt / "opencode.credentials.json").read_text(encoding="utf-8")
        self.assertIn("s3cret", text)
        self.assertIn("same-user readable", text)

    def test_identity_matches(self) -> None:
        rec = {"server_id": "srv_1", "server_nonce": "n", "pid": 9}
        self.assertTrue(identity_matches(rec, pid=9, server_id="srv_1"))
        self.assertFalse(identity_matches(rec, pid=8))


if __name__ == "__main__":
    unittest.main()
