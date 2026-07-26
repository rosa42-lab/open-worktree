"""Unit tests for runtime probe helpers (no live OpenCode required)."""

from __future__ import annotations

import unittest

from orch.runtime.adapter import CapabilityMatrix, OpenCodeRuntimeAdapter
from orch.runtime.http_client import OpenCodeHttpClient, _parse_sse_frame
from orch.runtime.probe import CANDIDATE_MIN_VERSION, _version_tuple


class RuntimeHelperTests(unittest.TestCase):
    def test_version_tuple(self) -> None:
        self.assertEqual(_version_tuple("1.18.5"), (1, 18, 5))
        self.assertGreaterEqual(
            _version_tuple("1.18.5"), _version_tuple(CANDIDATE_MIN_VERSION)
        )
        self.assertLess(_version_tuple("1.17.20"), _version_tuple("1.18.5"))

    def test_capability_required_pass(self) -> None:
        empty = CapabilityMatrix(
            **{name: False for name in CapabilityMatrix.__dataclass_fields__}
        )
        self.assertFalse(empty.required_pass)
        full = CapabilityMatrix(
            global_health=True,
            directory_header=True,
            directory_query=True,
            create_session=True,
            get_session=True,
            session_status=True,
            event_sse=True,
            abort=True,
            instance_dispose=True,
            prompt_async=True,
            session_fork_api=True,
            attach_cli_dir=True,
            attach_cli_session=True,
            attach_cli_fork=True,
            basic_auth=True,
            path_api=True,
            vcs_api=True,
            shell_api=True,
        )
        self.assertTrue(full.required_pass)

    def test_attach_command_quoting(self) -> None:
        client = OpenCodeHttpClient("http://127.0.0.1:4096")
        adapter = OpenCodeRuntimeAdapter(client)
        cmd = adapter.build_attach_command(
            r"E:\tmp\work tree",
            "ses_abc",
            fork=True,
        )
        self.assertIn("opencode attach http://127.0.0.1:4096", cmd)
        self.assertIn("--session ses_abc", cmd)
        self.assertIn("--fork", cmd)
        self.assertIn('"E:\\tmp\\work tree"', cmd)

    def test_parse_sse_frame(self) -> None:
        frame = 'event: message\ndata: {"type":"server.connected","properties":{}}'
        parsed = _parse_sse_frame(frame)
        assert parsed is not None
        self.assertEqual(parsed["event"], "message")
        self.assertEqual(parsed["data"]["type"], "server.connected")


if __name__ == "__main__":
    unittest.main()
