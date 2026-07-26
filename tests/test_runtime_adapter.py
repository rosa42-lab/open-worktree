"""V12-005 OpenCodeRuntimeAdapter protocol tests with fake Server."""

from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from orch.runtime.adapter import CapabilityMatrix, RuntimeAdapter
from orch.runtime.http_client import OpenCodeHttpClient
from orch.runtime.opencode import OpenCodeRuntimeAdapter


class _FakeHandler(BaseHTTPRequestHandler):
    server: "FakeOpenCodeServer"  # type: ignore[assignment]

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def _read_body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _record(self, method: str) -> None:
        self.server.requests.append(
            {
                "method": method,
                "path": self.path,
                "directory": self.headers.get("x-opencode-directory"),
                "authorization": self.headers.get("Authorization"),
                "body": self._read_body(),
            }
        )

    def do_GET(self) -> None:  # noqa: N802
        self._record("GET")
        parsed = urlparse(self.path)
        if parsed.path == "/global/health":
            self._json(200, {"healthy": True, "version": "1.18.5"})
            return
        if parsed.path == "/session/status":
            self._json(200, {"busy": False})
            return
        if parsed.path.startswith("/session/"):
            sid = parsed.path.split("/")[-1]
            self._json(200, {"id": sid, "title": "t"})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        body = self._read_body()
        # re-record with body (do_GET style would miss; overwrite last)
        self.server.requests.append(
            {
                "method": "POST",
                "path": self.path,
                "directory": self.headers.get("x-opencode-directory"),
                "authorization": self.headers.get("Authorization"),
                "body": body,
            }
        )
        parsed = urlparse(self.path)
        if parsed.path == "/session":
            self._json(200, {"id": "ses_fake_1", "title": "created"})
            return
        if parsed.path.endswith("/abort"):
            self._json(200, {"ok": True})
            return
        if parsed.path == "/instance/dispose":
            self._json(200, {"ok": True})
            return
        if parsed.path.endswith("/prompt_async"):
            self.send_response(204)
            self.end_headers()
            return
        self._json(404, {"error": "not found"})

    def _json(self, status: int, payload: Any) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class FakeOpenCodeServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int]) -> None:
        super().__init__(address, _FakeHandler)
        self.requests: list[dict[str, Any]] = []


class RuntimeAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.httpd = FakeOpenCodeServer(("127.0.0.1", 0))
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.port}"
        self.client = OpenCodeHttpClient(self.base, password="secret-pass")
        self.adapter = OpenCodeRuntimeAdapter(self.client)

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def test_protocol_conformance(self) -> None:
        self.assertIsInstance(self.adapter, RuntimeAdapter)

    def test_health_and_directory_routing(self) -> None:
        h = self.adapter.health()
        self.assertTrue(h.get("healthy"))
        sess = self.adapter.create_session("E:/wt-a", title="x")
        self.assertEqual(sess["id"], "ses_fake_1")
        dirs = [r.get("directory") for r in self.httpd.requests if r["method"] == "POST"]
        self.assertIn("E:/wt-a", dirs)

    def test_control_methods_recorded(self) -> None:
        self.adapter.abort("E:/wt-a", "ses_1")
        self.adapter.dispose_instance("E:/wt-a")
        self.adapter.send_prompt_async("E:/wt-a", "ses_1", text="hi")
        methods = {(r["method"], r["path"].split("?")[0]) for r in self.httpd.requests}
        self.assertIn(("POST", "/session/ses_1/abort"), methods)
        self.assertIn(("POST", "/instance/dispose"), methods)
        self.assertIn(("POST", "/session/ses_1/prompt_async"), methods)

    def test_auth_header_not_in_attach_command(self) -> None:
        cmd = self.adapter.build_attach_command("E:/wt a", "ses_1", fork=True)
        self.assertIn("opencode attach", cmd)
        self.assertIn("--fork", cmd)
        self.assertNotIn("secret-pass", cmd)
        self.assertNotIn("Authorization", cmd)

    def test_capabilities_matrix(self) -> None:
        caps = self.adapter.capabilities()
        self.assertIsInstance(caps, CapabilityMatrix)
        self.assertTrue(caps.global_health)
        self.assertTrue(caps.basic_auth)


if __name__ == "__main__":
    unittest.main()
