"""End-to-end happy path against temp HOME and bare repo."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers.git_fixture import make_bare_with_develop, run


class IntegrationFlowTests(unittest.TestCase):
    def test_add_init_worktree_enqueue_merge(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            proj = make_bare_with_develop(Path(td) / "proj")

            env = os.environ.copy()
            env["USERPROFILE"] = str(home)  # Windows Path.home()
            env["HOME"] = str(home)

            def orch(*args: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [os.environ.get("PYTHON", "python"), "-m", "orch", *args],
                    capture_output=True,
                    text=True,
                    env=env,
                    cwd=str(Path(__file__).resolve().parents[1]),
                )

            # Patch Path.home inside process is hard; set both HOME and use mock in-process
            with mock.patch("pathlib.Path.home", return_value=home):
                from orch.cli import main

                self.assertEqual(main(["project", "add", "alpha", str(proj)]), 0)
                self.assertEqual(main(["alpha", "init"]), 0)
                self.assertEqual(
                    main(["alpha", "worktree-add", "agentA", "feat/one"]), 0
                )
                wt = proj / "worktrees" / "agentA-feat__one"
                # commit a change
                (wt / "feature.txt").write_text("x\n", encoding="utf-8")
                run(["git", "add", "feature.txt"], cwd=wt)
                run(["git", "commit", "-m", "feat"], cwd=wt)

                self.assertEqual(
                    main(
                        [
                            "alpha",
                            "enqueue",
                            "agentA",
                            "feat/one",
                            str(wt),
                            "--priority",
                            "1",
                        ]
                    ),
                    0,
                )
                # json pending
                import io
                from contextlib import redirect_stdout

                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = main(["alpha", "pending", "--json"])
                self.assertEqual(code, 0)
                payload = json.loads(buf.getvalue())
                self.assertTrue(payload["ok"])
                self.assertEqual(len(payload["data"]["tasks"]), 1)

                self.assertEqual(main(["alpha", "merge", "--once"]), 0)

                buf2 = io.StringIO()
                with redirect_stdout(buf2):
                    code = main(["alpha", "list", "--json"])
                self.assertEqual(code, 0)
                listing = json.loads(buf2.getvalue())
                statuses = [t["status"] for t in listing["data"]["tasks"]]
                self.assertIn("merged", statuses)


if __name__ == "__main__":
    unittest.main()
