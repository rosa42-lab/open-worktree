"""V12-009 control lease tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from orch.db import connect, init_schema
from orch.errors import ValidationError
from orch.runtime.lease import (
    acquire_lease,
    assert_write_allowed,
    generate_lease_token,
    get_lease,
    hash_lease_token,
    release_lease,
    renew_lease,
    verify_lease_token,
)


class LeaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "t.db"
        self.conn = connect(self.db)
        init_schema(self.conn)
        self.conn.execute(
            """
            INSERT INTO agent_runs (
              id, project_name, agent_name, branch_name, worktree_path,
              runtime_kind, runtime_server_id, state, desired_state,
              observed_state, controller, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "run_1",
                "p",
                "a",
                "b",
                "/wt",
                "opencode",
                "srv",
                "running",
                "running",
                "idle",
                "agent",
                "t",
                "t",
            ),
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_token_hash_roundtrip(self) -> None:
        token = generate_lease_token()
        h = hash_lease_token("run_1", 1, token)
        self.assertTrue(verify_lease_token("run_1", 1, token, h))
        self.assertFalse(verify_lease_token("run_1", 2, token, h))

    def test_acquire_renew_release(self) -> None:
        token = acquire_lease(
            self.conn, run_id="run_1", controller="agent", generation=1
        )
        row = get_lease(self.conn, "run_1")
        assert row is not None
        self.assertNotEqual(row["token_hash"], token)
        renew_lease(
            self.conn, run_id="run_1", generation=1, token=token, ttl_sec=60
        )
        assert_write_allowed(
            self.conn, run_id="run_1", generation=1, token=token
        )
        with self.assertRaises(ValidationError):
            assert_write_allowed(
                self.conn, run_id="run_1", generation=2, token=token
            )
        release_lease(self.conn, run_id="run_1")
        self.assertIsNone(get_lease(self.conn, "run_1"))

    def test_plaintext_not_in_db(self) -> None:
        token = acquire_lease(
            self.conn, run_id="run_1", controller="agent", generation=3
        )
        dump = " ".join(
            str(x)
            for x in self.conn.execute(
                "SELECT * FROM control_leases"
            ).fetchone()
        )
        self.assertNotIn(token, dump)


if __name__ == "__main__":
    unittest.main()
