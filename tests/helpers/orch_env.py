"""In-process orch test environment with patched HOME."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any
from unittest import mock

from tests.helpers.git_fixture import make_bare_with_develop, run


class OrchEnv:
    def __init__(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.home = Path(self._td.name) / "home"
        self.home.mkdir()
        self.proj = make_bare_with_develop(Path(self._td.name) / "proj")
        self._home_patch = mock.patch("pathlib.Path.home", return_value=self.home)
        self._home_patch.start()
        # re-import constants users of home — registry uses Path.home at call time via constants helpers
        from orch.cli import main

        self.main = main

    def close(self) -> None:
        self._home_patch.stop()
        self._td.cleanup()

    def __enter__(self) -> OrchEnv:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def run(self, *args: str) -> int:
        """Run command with --json, discard stdout (quiet tests)."""
        code, _ = self.run_json(*args)
        return code

    def run_json(self, *args: str) -> tuple[int, dict[str, Any]]:
        argv = list(args)
        if "--json" not in argv:
            argv.append("--json")
        buf = io.StringIO()
        err = io.StringIO()
        # Human stderr errors still go to real stderr; keep JSON on stdout only.
        with redirect_stdout(buf):
            code = self.main(argv)
        text = buf.getvalue().strip()
        # Some failures print usage to stdout via argparse; tolerate empty
        if not text:
            return code, {}
        # Last JSON object if mixed noise
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # try last line
            lines = [ln for ln in text.splitlines() if ln.strip().startswith("{")]
            data = json.loads(lines[-1]) if lines else {}
        return code, data

    def setup_project(self, name: str = "alpha") -> str:
        assert self.run("project", "add", name, str(self.proj)) == 0
        assert self.run(name, "init") == 0
        return name

    def add_feature_branch(
        self,
        project: str,
        agent: str,
        branch: str,
        filename: str,
        content: str,
    ) -> Path:
        assert self.run(project, "worktree-add", agent, branch) == 0
        safe = branch.replace("/", "__")
        wt = self.proj / "worktrees" / f"{agent}-{safe}"
        (wt / filename).write_text(content, encoding="utf-8")
        run(["git", "add", filename], cwd=wt)
        run(["git", "commit", "-m", f"add {filename}"], cwd=wt)
        return wt


class OrchEnvTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.env = OrchEnv()
        self.project = self.env.setup_project("alpha")

    def tearDown(self) -> None:
        self.env.close()
