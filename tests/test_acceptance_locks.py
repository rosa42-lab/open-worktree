"""§17.13 lock semantics (single-process approximation)."""

from __future__ import annotations

import os
from pathlib import Path

from orch.constants import project_lock_path
from orch.db import open_project_db
from orch.errors import LockError
from orch.locks import acquire, lock_break, lock_status, release
from tests.helpers.orch_env import OrchEnvTestCase


class LockAcceptanceTests(OrchEnvTestCase):
    def test_lock_status_and_break_live_pid_refused(self) -> None:
        project = self.project
        conn = open_project_db(project, init=True)
        try:
            handle = acquire(
                project_lock_path(project),
                command="test",
                project=project,
                audit_conn=conn,
            )
            st = lock_status(project_lock_path(project))
            self.assertTrue(st["locked"])
            self.assertEqual(st["owner"]["pid"], os.getpid())

            with self.assertRaises(LockError) as ctx:
                lock_break(
                    project_lock_path(project),
                    force=True,
                    audit_conn=conn,
                    project=project,
                )
            self.assertEqual(ctx.exception.code, 6)

            release(handle, audit_conn=conn)
            st2 = lock_status(project_lock_path(project))
            self.assertFalse(st2["locked"])
        finally:
            conn.close()

    def test_lock_break_dead_pid(self) -> None:
        import json
        import socket

        project = self.project
        path = project_lock_path(project)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Fabricate lock for a dead PID (valid JSON)
        path.write_text(
            json.dumps(
                {
                    "token": "deadbeef",
                    "pid": 999999,
                    "hostname": socket.gethostname(),
                    "started_at": "2020-01-01T00:00:00Z",
                    "command": "x",
                    "project": project,
                }
            ),
            encoding="utf-8",
        )
        conn = open_project_db(project, init=True)
        try:
            result = lock_break(path, force=True, audit_conn=conn, project=project)
            self.assertTrue(result["removed"])
            self.assertFalse(path.exists())
        finally:
            conn.close()

    def test_cli_lock_status_json(self) -> None:
        code, payload = self.env.run_json(self.project, "lock-status", "--json")
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertIn("locked", payload["data"])
