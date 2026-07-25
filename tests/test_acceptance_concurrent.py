"""§17.1 concurrent merge; concurrent-ish enqueue race via two processes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers.git_fixture import make_bare_with_develop, run


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _orch_env(home: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["USERPROFILE"] = str(home)
    env["HOME"] = str(home)
    env["PYTHONPATH"] = str(_repo_root())
    # Prefer utf-8 for child JSON
    env["PYTHONIOENCODING"] = "utf-8"
    # Ensure merge commits work without global git identity
    env.setdefault("GIT_AUTHOR_NAME", "Test")
    env.setdefault("GIT_AUTHOR_EMAIL", "test@example.com")
    env.setdefault("GIT_COMMITTER_NAME", "Test")
    env.setdefault("GIT_COMMITTER_EMAIL", "test@example.com")
    return env


def _orch(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "orch", *args],
        cwd=str(_repo_root()),
        env=_orch_env(home),
        capture_output=True,
        text=True,
        timeout=120,
    )


class ConcurrentMergeTests(unittest.TestCase):
    def test_two_processes_merge_once_serial_order(self) -> None:
        """Two shells merge --once --json: both succeed; one task each; order by queue."""
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            proj = make_bare_with_develop(Path(td) / "proj")

            # bootstrap via subprocess so registry lives under home
            r = _orch(home, "project", "add", "alpha", str(proj), "--json")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            r = _orch(home, "alpha", "init", "--json")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)

            for agent, branch, fname in (
                ("agentA", "feat/c1", "c1.txt"),
                ("agentB", "feat/c2", "c2.txt"),
            ):
                r = _orch(home, "alpha", "worktree-add", agent, branch, "--json")
                self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
                safe = branch.replace("/", "__")
                wt = proj / "worktrees" / f"{agent}-{safe}"
                (wt / fname).write_text(f"{fname}\n", encoding="utf-8")
                run(["git", "add", fname], cwd=wt)
                run(["git", "commit", "-m", fname], cwd=wt)
                r = _orch(
                    home,
                    "alpha",
                    "enqueue",
                    agent,
                    branch,
                    str(wt),
                    "--priority",
                    "1",
                    "--json",
                )
                self.assertEqual(r.returncode, 0, r.stderr + r.stdout)

            results: list[subprocess.CompletedProcess[str]] = []
            barriers = threading.Barrier(2)

            def worker() -> None:
                barriers.wait(timeout=30)
                results.append(
                    _orch(home, "alpha", "merge", "--once", "--json")
                )

            t1 = threading.Thread(target=worker)
            t2 = threading.Thread(target=worker)
            t1.start()
            t2.start()
            t1.join(timeout=120)
            t2.join(timeout=120)
            self.assertEqual(len(results), 2)

            payloads = []
            for r in results:
                self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
                payloads.append(json.loads(r.stdout))
                self.assertTrue(payloads[-1]["ok"])
                self.assertEqual(len(payloads[-1]["data"]["processed"]), 1)
                self.assertEqual(
                    payloads[-1]["data"]["processed"][0]["status"], "merged"
                )

            ids = {
                payloads[0]["data"]["processed"][0]["task_id"],
                payloads[1]["data"]["processed"][0]["task_id"],
            }
            self.assertEqual(len(ids), 2, "each process must claim a different task")

            # queue empty now
            r = _orch(home, "alpha", "pending", "--json")
            self.assertEqual(r.returncode, 0)
            pending = json.loads(r.stdout)
            self.assertEqual(pending["data"]["tasks"], [])


class ConcurrentEnqueueTests(unittest.TestCase):
    def test_double_enqueue_same_branch_one_wins(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            proj = make_bare_with_develop(Path(td) / "proj")
            self.assertEqual(
                _orch(home, "project", "add", "alpha", str(proj)).returncode, 0
            )
            self.assertEqual(_orch(home, "alpha", "init").returncode, 0)
            self.assertEqual(
                _orch(home, "alpha", "worktree-add", "agentA", "feat/race").returncode,
                0,
            )
            wt = proj / "worktrees" / "agentA-feat__race"
            (wt / "r.txt").write_text("r\n", encoding="utf-8")
            run(["git", "add", "r.txt"], cwd=wt)
            run(["git", "commit", "-m", "r"], cwd=wt)

            results: list[subprocess.CompletedProcess[str]] = []
            bar = threading.Barrier(2)

            def worker() -> None:
                bar.wait(timeout=30)
                results.append(
                    _orch(
                        home,
                        "alpha",
                        "enqueue",
                        "agentA",
                        "feat/race",
                        str(wt),
                        "--json",
                    )
                )

            threads = [threading.Thread(target=worker) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=60)

            codes = sorted(r.returncode for r in results)
            self.assertEqual(codes, [0, 7])
            ok = [json.loads(r.stdout) for r in results if r.returncode == 0]
            self.assertEqual(len(ok), 1)
