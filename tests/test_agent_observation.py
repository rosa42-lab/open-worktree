"""V12-006 observe-only command tests (§19.11)."""

from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest import mock
from urllib.parse import urlparse

from orch.commands.agent_readonly import (
    build_observe_adapter,
    cmd_agent_list,
    cmd_agent_register,
    cmd_agent_show,
    cmd_agent_watch,
)
from orch.db import connect, init_schema
from orch.constants import project_lock_path


class _RecHandler(BaseHTTPRequestHandler):
    server: "RecServer"  # type: ignore[assignment]

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def do_GET(self) -> None:  # noqa: N802
        self.server.requests.append(
            {
                "method": "GET",
                "path": self.path,
                "directory": self.headers.get("x-opencode-directory"),
            }
        )
        parsed = urlparse(self.path)
        if parsed.path == "/global/health":
            body = b'{"healthy":true}'
        elif parsed.path == "/session/status":
            body = b'{"busy":false}'
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        _ = self.rfile.read(n) if n else b""
        self.server.requests.append(
            {
                "method": "POST",
                "path": self.path,
                "directory": self.headers.get("x-opencode-directory"),
            }
        )
        self.send_response(200)
        self.end_headers()


class RecServer(ThreadingHTTPServer):
    def __init__(self, addr: tuple[str, int]) -> None:
        super().__init__(addr, _RecHandler)
        self.requests: list[dict[str, Any]] = []


class ObserveOnlyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.project = "obsproj"
        self.db_dir = self.home / ".orchestrator" / "data" / self.project
        self.db_dir.mkdir(parents=True)
        self.db_path = self.db_dir / "orchestrator.db"

        self.httpd = RecServer(("127.0.0.1", 0))
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.port}"

        self.home_patch = mock.patch("pathlib.Path.home", return_value=self.home)
        self.home_patch.start()
        # also patch constants helpers that cache? they use Path.home() each call usually
        from orch import constants as C

        self.const_patch = mock.patch.object(
            C, "orchestrator_home", lambda: self.home / ".orchestrator"
        )
        # orchestrator_home may be a function - check
        if callable(getattr(C, "orchestrator_home", None)):
            self.const_patch = mock.patch.object(
                C, "orchestrator_home", lambda: self.home / ".orchestrator"
            )
            self.const_patch.start()
        else:
            self.const_patch = None

        # Ensure DB path resolves under temp home via project_db_path
        self.path_patch = mock.patch(
            "orch.constants.project_db_path",
            return_value=self.db_path,
        )
        self.data_patch = mock.patch(
            "orch.constants.project_data_dir",
            return_value=self.db_dir,
        )
        self.path_patch.start()
        self.data_patch.start()

        conn = connect(self.db_path)
        init_schema(conn)
        conn.close()

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.path_patch.stop()
        self.data_patch.stop()
        self.home_patch.stop()
        if self.const_patch:
            self.const_patch.stop()
        self.tmp.cleanup()

    def test_register_list_show_watch_no_control_posts(self) -> None:
        lock_path = project_lock_path(self.project)
        self.assertFalse(lock_path.exists())

        reg = cmd_agent_register(
            self.project,
            agent="agentA",
            branch="feat/a",
            worktree_path=r"E:\orch-h2-probe\worktree-a",
            session_id="ses_obs_1",
            runtime_server_id="srv_test",
            base_url=self.base_url,
        )
        run_id = reg["run"]["run_id"]
        self.assertTrue(run_id.startswith("run_"))

        listed = cmd_agent_list(self.project, base_url=self.base_url)
        self.assertEqual(len(listed["runs"]), 1)
        self.assertEqual(listed["runs"][0]["session_id"], "ses_obs_1")

        shown = cmd_agent_show(self.project, run_id, base_url=self.base_url)
        self.assertEqual(shown["run"]["desired_state"], "stopped")

        # Snapshot desired state before watch
        before_desired = shown["run"]["desired_state"]
        before_state = shown["run"]["state"]

        adapter = build_observe_adapter(self.base_url)
        buf = io.StringIO()
        result = cmd_agent_watch(
            self.project,
            run_id,
            base_url=self.base_url,
            max_ticks=2,
            interval_sec=0.0,
            as_jsonl=True,
            stream=buf,
            adapter=adapter,
        )
        self.assertIsNone(result)
        lines = [json.loads(x) for x in buf.getvalue().splitlines() if x.strip()]
        self.assertEqual(lines[0]["type"], "stream_header")
        self.assertEqual(lines[0]["schema_version"], 1)
        self.assertEqual(lines[-1]["type"], "stream_footer")

        after = cmd_agent_show(self.project, run_id)
        self.assertEqual(after["run"]["desired_state"], before_desired)
        self.assertEqual(after["run"]["state"], before_state)

        # Observe-only: GET status allowed; no POST control verbs
        posts = [r for r in self.httpd.requests if r["method"] == "POST"]
        self.assertEqual(posts, [])
        for r in self.httpd.requests:
            path = r["path"].split("?")[0]
            self.assertNotIn("/abort", path)
            self.assertNotIn("/dispose", path)
            self.assertNotIn("/prompt", path)

        # No project lock created by observe commands
        self.assertFalse(lock_path.exists())


if __name__ == "__main__":
    unittest.main()
