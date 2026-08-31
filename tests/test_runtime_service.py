"""V12-004/007/008 focused service tests without live OpenCode."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from orch.db import connect, init_schema
from orch.errors import ValidationError
from orch.runtime.service import runtime_stop


class RuntimeStopGuardTests(unittest.TestCase):
    def test_stop_blocked_when_active_runs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            data = home / ".orchestrator" / "data" / "p1"
            data.mkdir(parents=True)
            db = data / "orchestrator.db"
            conn = connect(db)
            init_schema(conn)
            conn.execute(
                """
                INSERT INTO agent_runs (
                  id, project_name, agent_name, branch_name, worktree_path,
                  runtime_kind, runtime_server_id, state, desired_state,
                  observed_state, controller, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "run_live",
                    "p1",
                    "a",
                    "b",
                    "/wt",
                    "opencode",
                    "srv",
                    "running",
                    "running",
                    "busy",
                    "agent",
                    "t",
                    "t",
                ),
            )
            conn.commit()
            conn.close()

            rt = home / ".orchestrator" / "runtime"
            rt.mkdir(parents=True)
            with mock.patch("orch.runtime.service.runtime_lock_path", return_value=rt / "opencode.lock"), \
                 mock.patch("orch.runtime.service.ensure_runtime_dirs"), \
                 mock.patch("orch.runtime.service.load_registry", return_value={
                     "managed_by_orch": True,
                     "pid": 1,
                     "hostname": "x",
                     "base_url": "http://127.0.0.1:4096",
                     "server_id": "srv",
                 }), \
                 mock.patch("orch.runtime.service.count_active_agent_runs", return_value=1), \
                 mock.patch("orch.runtime.service.acquire") as acq, \
                 mock.patch("orch.runtime.service.release"):
                acq.return_value = mock.Mock()
                with self.assertRaises(ValidationError) as ctx:
                    runtime_stop(force=False)
                self.assertEqual(ctx.exception.kind, "runtime_stop_blocked")

    def test_stop_drains_then_fences_then_kills(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            orch_home = home / ".orchestrator"
            (orch_home / "data" / "alpha").mkdir(parents=True)
            rt = orch_home / "runtime"
            rt.mkdir(parents=True)
            store = [
                {
                    "managed_by_orch": True,
                    "pid": 0,
                    "hostname": "x",
                    "base_url": "http://127.0.0.1:4096",
                    "server_id": "srv",
                    "state": "running",
                }
            ]
            commands: list[str] = []

            def rec_acquire(path, **kwargs):
                commands.append(str(kwargs.get("command")))
                return mock.Mock(
                    path=Path(str(path)), token="t", payload={}, family=None
                )

            def load():
                return dict(store[0])

            def save(rec):
                store[0] = dict(rec)

            with mock.patch("pathlib.Path.home", return_value=home), \
                 mock.patch("orch.runtime.service.runtime_lock_path", return_value=rt / "opencode.lock"), \
                 mock.patch("orch.runtime.service.ensure_runtime_dirs"), \
                 mock.patch("orch.runtime.service.load_registry", side_effect=load), \
                 mock.patch("orch.runtime.service.save_registry", side_effect=save), \
                 mock.patch("orch.runtime.service.count_active_agent_runs", return_value=0), \
                 mock.patch("orch.runtime.service.acquire", side_effect=rec_acquire), \
                 mock.patch("orch.runtime.service.release"), \
                 mock.patch("orch.runtime.service.registry_owner_alive", return_value=False), \
                 mock.patch("orch.runtime.service.public_registry_view", return_value={}):
                out = runtime_stop(force=False)
            self.assertEqual(out["action"], "stopped")
            self.assertEqual(store[0]["state"], "stopped")
            self.assertEqual(
                commands,
                ["runtime.stop", "runtime.stop.fence", "runtime.stop.kill"],
            )


if __name__ == "__main__":
    unittest.main()
