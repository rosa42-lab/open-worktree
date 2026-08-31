"""V20: executor binary identity and Class B spawn argv (no live Claude)."""

from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from orch.db import open_project_db
from orch.errors import ValidationError
from orch.runtime.binary import resolve_executor, spawn_env
from tests.helpers.orch_env import OrchEnvTestCase


def _write_fake_claude(td: Path, *, exit_code: int = 0) -> Path:
    path = td / "fake_claude.py"
    path.write_text(
        "\n".join(
            [
                "import json, sys",
                "from pathlib import Path",
                "log = Path(__file__).with_name('argv.jsonl')",
                "with log.open('a', encoding='utf-8') as fh:",
                "    fh.write(json.dumps(sys.argv[1:]) + '\\n')",
                f"raise SystemExit({int(exit_code)})",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _logged_argv(log: Path) -> list[list[str]]:
    lines = [ln for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return [json.loads(ln) for ln in lines]


class BinaryIdentityTests(unittest.TestCase):
    def test_unknown_kind_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fake = Path(td) / "claude.exe"
            fake.write_text("", encoding="utf-8")
            with self.assertRaises(ValidationError) as ctx:
                resolve_executor(kind="cursor-agent", path=str(fake))
            self.assertEqual(ctx.exception.kind, "runtime_unknown_kind")

    def test_bare_command_name_rejected(self) -> None:
        with self.assertRaises(ValidationError) as ctx:
            resolve_executor(kind="claude", path="claude")
        self.assertEqual(ctx.exception.kind, "runtime_binary_not_absolute")

    def test_agent_exe_is_not_claude(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fake = Path(td) / "agent.exe"
            fake.write_text("", encoding="utf-8")
            with self.assertRaises(ValidationError) as ctx:
                resolve_executor(kind="claude", path=str(fake))
            self.assertEqual(ctx.exception.kind, "runtime_binary_identity")
            grok = resolve_executor(kind="grok", path=str(fake))
            self.assertEqual(grok, fake.resolve())

    def test_spawn_env_is_allowlisted(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fake = Path(td) / "claude.exe"
            fake.write_text("", encoding="utf-8")
            env = spawn_env(
                binary=fake,
                extra={"ANTHROPIC_API_KEY": "secret", "OPENCODE_SERVER_PASSWORD": "nope"},
            )
            self.assertEqual(env.get("ANTHROPIC_API_KEY"), "secret")
            self.assertNotIn("OPENCODE_SERVER_PASSWORD", env)
            self.assertTrue(str(fake.parent) in env.get("PATH", ""))


class ClassBClaudeArgvTests(unittest.TestCase):
    def test_first_spawn_uses_print_and_session_id(self) -> None:
        from orch.runtime.claude import run_slice

        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw)
            binary = _write_fake_claude(td)
            result = run_slice(
                kind="claude",
                binary=str(binary),
                prompt="hello slice",
            )
            argv = _logged_argv(td / "argv.jsonl")[0]
            self.assertIn("-p", argv)
            self.assertIn("--session-id", argv)
            sid = argv[argv.index("--session-id") + 1]
            uuid.UUID(sid)
            self.assertEqual(result.session_id, sid)
            self.assertEqual(result.exit_code, 0)
            self.assertFalse(result.running)
            self.assertIn("--output-format", argv)
            self.assertIn("text", argv)
            self.assertNotIn("--bg", argv)
            self.assertNotIn("-c", argv)
            self.assertNotIn("--continue", argv)
            self.assertIn("--", argv)
            self.assertEqual(argv[argv.index("--") + 1], "hello slice")

    def test_continue_uses_resume_same_id(self) -> None:
        from orch.runtime.claude import run_slice

        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw)
            binary = _write_fake_claude(td)
            first = run_slice(
                kind="claude",
                binary=str(binary),
                prompt="first",
            )
            second = run_slice(
                kind="claude",
                binary=str(binary),
                prompt="second",
                session_id=first.session_id,
            )
            rows = _logged_argv(td / "argv.jsonl")
            self.assertEqual(len(rows), 2)
            cont = rows[1]
            self.assertIn("-p", cont)
            self.assertIn("--resume", cont)
            self.assertEqual(cont[cont.index("--resume") + 1], first.session_id)
            self.assertNotIn("--session-id", cont)
            self.assertEqual(second.session_id, first.session_id)
            self.assertFalse(second.running)

    def test_nonzero_exit_fail_closed(self) -> None:
        from orch.runtime.claude import run_slice

        with tempfile.TemporaryDirectory() as raw:
            td = Path(raw)
            binary = _write_fake_claude(td, exit_code=2)
            with self.assertRaises(ValidationError) as ctx:
                run_slice(
                    kind="claude",
                    binary=str(binary),
                    prompt="nope",
                )
            self.assertEqual(ctx.exception.kind, "runtime_slice_failed")
            self.assertEqual(ctx.exception.details.get("exit_code"), 2)

    def test_does_not_use_opencode_http_client(self) -> None:
        import inspect

        import orch.runtime.claude as claude_mod

        source = inspect.getsource(claude_mod)
        self.assertNotIn("OpenCodeHttpClient", source)
        self.assertIsNone(getattr(claude_mod, "OpenCodeHttpClient", None))
        with mock.patch("orch.runtime.http_client.OpenCodeHttpClient") as client_cls:
            from orch.runtime.claude import run_slice

            with tempfile.TemporaryDirectory() as raw:
                td = Path(raw)
                binary = _write_fake_claude(td)
                run_slice(kind="claude", binary=str(binary), prompt="x")
            client_cls.assert_not_called()


class ExecutorClassTests(unittest.TestCase):
    def test_class_for_kind(self) -> None:
        from orch.runtime.factory import class_for_kind

        self.assertEqual(class_for_kind("opencode"), "A")
        self.assertEqual(class_for_kind("claude"), "B")
        with self.assertRaises(ValidationError) as ctx:
            class_for_kind("grok")
        self.assertEqual(ctx.exception.kind, "runtime_class_not_wired")
        with self.assertRaises(ValidationError) as ctx:
            class_for_kind("codex")
        self.assertEqual(ctx.exception.kind, "runtime_class_not_wired")
        with self.assertRaises(ValidationError) as ctx:
            class_for_kind("cursor-agent")
        self.assertEqual(ctx.exception.kind, "runtime_unknown_kind")


class ClassBLifecycleTests(OrchEnvTestCase):
    def test_claude_kind_does_not_spawn_sse_worker(self) -> None:
        from orch.runtime.lifecycle import AgentLifecycleService

        wt = self.env.proj / "worktrees" / "coder-feat__cb"
        wt.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as raw:
            binary = _write_fake_claude(Path(raw))
            svc = AgentLifecycleService(self.project)
            with mock.patch("orch.runtime.lifecycle.spawn_worker_env") as spawn_env_fn:
                started = svc.start(
                    agent="coder",
                    branch="feat/cb",
                    worktree_path=str(wt),
                    prompt="do slice",
                    runtime_kind="claude",
                    executor_path=str(binary),
                )
            spawn_env_fn.assert_not_called()
            argv = _logged_argv(Path(raw) / "argv.jsonl")[0]
        run = started["run"]
        self.assertNotEqual(run["state"], "running")
        self.assertEqual(run["state"], "exited")
        self.assertTrue(run["session_id"])
        self.assertIn("-p", argv)
        self.assertIn("--session-id", argv)
        conn = open_project_db(self.project, init=False)
        try:
            row = conn.execute(
                "SELECT runtime_kind, state, session_id FROM agent_runs WHERE id = ?",
                (run["run_id"],),
            ).fetchone()
            self.assertEqual(row["runtime_kind"], "claude")
            self.assertEqual(row["state"], "exited")
            self.assertEqual(row["session_id"], run["session_id"])
        finally:
            conn.close()

    def test_failed_slice_is_not_running(self) -> None:
        from orch.runtime.lifecycle import AgentLifecycleService

        wt = self.env.proj / "worktrees" / "coder-feat__fail"
        wt.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as raw:
            binary = _write_fake_claude(Path(raw), exit_code=2)
            svc = AgentLifecycleService(self.project)
            with self.assertRaises(ValidationError) as ctx:
                svc.start(
                    agent="coder",
                    branch="feat/fail",
                    worktree_path=str(wt),
                    prompt="nope",
                    runtime_kind="claude",
                    executor_path=str(binary),
                )
        self.assertEqual(ctx.exception.kind, "runtime_slice_failed")
        conn = open_project_db(self.project, init=False)
        try:
            rows = list(
                conn.execute("SELECT state FROM agent_runs WHERE worktree_path LIKE ?", ("%fail%",))
            )
            self.assertTrue(rows)
            for row in rows:
                self.assertNotEqual(row["state"], "running")
        finally:
            conn.close()

    def test_topic_start_without_start_session_does_not_spawn(self) -> None:
        from orch.commands.topic import coordinator_bind, topic_start

        coordinator_bind(
            self.project,
            session_id="ses_coord",
            directory=str(self.env.proj),
        )
        with mock.patch("orch.runtime.lifecycle.AgentLifecycleService") as svc_cls:
            out = topic_start(
                self.project,
                name="nosess",
                title="No session",
                goal="ship",
                branch_name="feat/nosess",
                agent_name="coder",
                provision_session=False,
            )
        self.assertEqual(out["topic"]["lifecycle_state"], "proposed")
        svc_cls.assert_not_called()

