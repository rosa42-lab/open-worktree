"""V19: unknown capability = false, adapter injection, dual-lock, gates."""

from __future__ import annotations

import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from orch.commands.agent_readonly import cmd_agent_list
from orch.constants import PROJECT_LOCK_NAME, RUNTIME_LOCK_NAME
from orch.db import open_project_db
from orch.errors import ValidationError
from orch.locks import acquire, release
from orch.runtime.adapter import CapabilityMatrix
from orch.runtime.gate import assert_runtime_gate
from orch.runtime.http_client import OpenCodeHttpClient
from orch.runtime.lifecycle import AgentLifecycleService
from orch.runtime.opencode import OpenCodeRuntimeAdapter
from orch.runtime.worker import write_heartbeat
from tests.helpers.orch_env import OrchEnvTestCase
from tests.test_runtime_adapter import FakeOpenCodeServer


class RecordingAdapter:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def health(self) -> dict:
        self.calls.append("health")
        return {"healthy": True, "version": "fake-1"}

    def capabilities(self) -> CapabilityMatrix:
        return replace(CapabilityMatrix.unknown(), global_health=True)

    def create_session(self, directory: str, *, title: str | None = None) -> dict:
        self.calls.append("create_session")
        return {"id": "ses_injected"}

    def get_session(self, directory: str, session_id: str) -> dict:
        self.calls.append("get_session")
        return {"id": session_id}

    def send_prompt_async(self, *args, **kwargs) -> None:
        self.calls.append("prompt_async")
        raise AssertionError("heartbeat/start must not send_prompt without prompt")

    def abort(self, *args, **kwargs) -> dict:
        self.calls.append("abort")
        raise AssertionError("agent-stop must not call abort")

    def dispose_instance(self, *args, **kwargs) -> dict:
        return {}

    def get_status(self, *args, **kwargs) -> dict:
        return {"busy": False}

    def subscribe_events(self, *args, **kwargs) -> list:
        return []

    def build_attach_command(self, *args, **kwargs) -> str:
        return "fake attach"


class CapabilityGateTests(unittest.TestCase):
    def test_unknown_operation_fail_closed(self) -> None:
        with self.assertRaises(ValidationError) as ctx:
            assert_runtime_gate("teleport", CapabilityMatrix.unknown())
        self.assertEqual(ctx.exception.kind, "runtime_capability_unknown")

    def test_agent_stop_local_does_not_require_abort(self) -> None:
        assert_runtime_gate("agent_stop_local", CapabilityMatrix.unknown())

    def test_agent_start_without_prompt_does_not_require_prompt_async(self) -> None:
        matrix = replace(CapabilityMatrix.unknown(), global_health=True)
        assert_runtime_gate("agent_start", matrix)
        with self.assertRaises(ValidationError) as ctx:
            assert_runtime_gate("agent_start_prompt", matrix)
        self.assertEqual(ctx.exception.kind, "runtime_capability_missing")
        self.assertEqual(ctx.exception.details.get("capability"), "prompt_async")

    def test_create_session_requires_cap(self) -> None:
        with self.assertRaises(ValidationError) as ctx:
            assert_runtime_gate("create_session", CapabilityMatrix.unknown())
        self.assertEqual(ctx.exception.kind, "runtime_capability_missing")

    def test_fork_requires_session_fork_api(self) -> None:
        with self.assertRaises(ValidationError) as ctx:
            assert_runtime_gate("fork", CapabilityMatrix.unknown())
        self.assertEqual(ctx.exception.kind, "runtime_capability_missing")
        self.assertEqual(ctx.exception.details.get("capability"), "session_fork_api")

    def test_attach_fork_requires_cap(self) -> None:
        with self.assertRaises(ValidationError) as ctx:
            assert_runtime_gate("attach_fork", CapabilityMatrix.unknown())
        self.assertEqual(ctx.exception.kind, "runtime_capability_missing")
        self.assertEqual(ctx.exception.details.get("capability"), "attach_cli_fork")


class UnknownCapabilitiesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.httpd = FakeOpenCodeServer(("127.0.0.1", 0))
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        port = self.httpd.server_address[1]
        self.client = OpenCodeHttpClient(f"http://127.0.0.1:{port}", password="x")
        self.adapter = OpenCodeRuntimeAdapter(self.client)

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def test_live_capabilities_are_false_except_health_and_auth(self) -> None:
        caps = self.adapter.capabilities()
        self.assertTrue(caps.global_health)
        self.assertTrue(caps.basic_auth)
        for name, value in caps.as_dict().items():
            if name in ("global_health", "basic_auth"):
                continue
            self.assertFalse(value, msg=name)


class ProbeCliTests(unittest.TestCase):
    def test_runtime_probe_help_lists_probe_full(self) -> None:
        from orch.cli import main
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["runtime", "probe", "--help"])
        self.assertEqual(code, 0)
        text = buf.getvalue()
        self.assertIn("--probe-full", text)
        self.assertIn("--allow-external-full", text)


class DualLockTests(unittest.TestCase):
    def test_cannot_hold_project_and_runtime_together(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            project = root / PROJECT_LOCK_NAME
            runtime = root / RUNTIME_LOCK_NAME
            h1 = acquire(project, command="t", project="p")
            try:
                with self.assertRaises(ValidationError) as ctx:
                    acquire(runtime, command="t", project=None)
                self.assertEqual(ctx.exception.kind, "dual_lock_forbidden")
            finally:
                release(h1)
            h2 = acquire(runtime, command="t", project=None)
            try:
                with self.assertRaises(ValidationError) as ctx:
                    acquire(project, command="t", project="p")
                self.assertEqual(ctx.exception.kind, "dual_lock_forbidden")
            finally:
                release(h2)


class ObserveNoHttpTests(OrchEnvTestCase):
    def test_agent_list_does_not_construct_http_client(self) -> None:
        with mock.patch(
            "orch.commands.agent_readonly.OpenCodeHttpClient"
        ) as client_cls:
            out = cmd_agent_list(self.project)
            self.assertEqual(out["runs"], [])
            client_cls.assert_not_called()


class InjectedAdapterLifecycleTests(OrchEnvTestCase):
    def test_start_heartbeat_stop_without_http(self) -> None:
        fake = RecordingAdapter()
        svc = AgentLifecycleService(self.project, adapter=fake)
        wt = self.env.proj / "worktrees" / "coder-feat__inj"
        wt.mkdir(parents=True, exist_ok=True)

        def on_popen(*_a, **_k):
            conn = open_project_db(self.project, init=False)
            try:
                row = conn.execute(
                    """
                    SELECT id, worker_nonce, controller_generation
                    FROM agent_runs ORDER BY created_at DESC LIMIT 1
                    """
                ).fetchone()
            finally:
                conn.close()
            proc = mock.Mock()
            proc.pid = 2147483646
            proc.poll.return_value = None
            write_heartbeat(
                self.project,
                row["id"],
                pid=proc.pid,
                nonce=str(row["worker_nonce"]),
                generation=int(row["controller_generation"]),
            )
            return proc

        with mock.patch("orch.runtime.lifecycle.subprocess.Popen", side_effect=on_popen):
            started = svc.start(
                agent="coder",
                branch="feat/inj",
                worktree_path=str(wt),
            )
        self.assertTrue(started.get("finalized"), msg=str(started))
        self.assertIn("create_session", fake.calls)
        self.assertNotIn("abort", fake.calls)
        self.assertNotIn("prompt_async", fake.calls)
        run_id = started["run"]["run_id"]
        conn = open_project_db(self.project, init=False)
        try:
            row = conn.execute(
                "SELECT capability_digest, runtime_version FROM agent_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            self.assertEqual(row["capability_digest"], fake.capabilities().digest())
            self.assertEqual(row["runtime_version"], "fake-1")
        finally:
            conn.close()
        stopped = svc.stop(run_id)
        self.assertEqual(stopped["run"]["state"], "exited")
        self.assertNotIn("abort", fake.calls)

    def test_start_with_prompt_requires_prompt_async(self) -> None:
        fake = RecordingAdapter()
        svc = AgentLifecycleService(self.project, adapter=fake)
        wt = self.env.proj / "worktrees" / "coder-feat__prompt"
        wt.mkdir(parents=True, exist_ok=True)
        with self.assertRaises(ValidationError) as ctx:
            svc.start(
                agent="coder",
                branch="feat/prompt",
                worktree_path=str(wt),
                prompt="do the thing",
            )
        self.assertEqual(ctx.exception.kind, "runtime_capability_missing")
        self.assertEqual(ctx.exception.details.get("capability"), "prompt_async")
