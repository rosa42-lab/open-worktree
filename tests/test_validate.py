from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

from orch.errors import UsageError
from orch.validate import (
    branch_safe_name,
    canonical_worktree_path,
    normalize_path,
    validate_agent_name,
    validate_project_name,
)


class ValidateTests(unittest.TestCase):
    def test_project_ok(self) -> None:
        self.assertEqual(validate_project_name("alpha"), "alpha")

    def test_project_bad(self) -> None:
        with self.assertRaises(UsageError):
            validate_project_name("../etc")
        with self.assertRaises(UsageError):
            validate_project_name("")

    def test_agent_ok(self) -> None:
        validate_agent_name("agent.1")

    def test_path_null(self) -> None:
        with self.assertRaises(UsageError):
            normalize_path("foo\x00bar")

    def test_branch_safe(self) -> None:
        self.assertEqual(branch_safe_name("feat/x"), "feat__x")

    def test_canonical_worktree_path_variants(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td).resolve()
            sep = "\\" if os.name == "nt" else "/"
            keys = {
                canonical_worktree_path(str(p)),
                canonical_worktree_path(p.as_posix()),
                canonical_worktree_path(str(p) + sep),
            }
            self.assertEqual(len(keys), 1, msg=keys)
            missing = str(p / "does-not-exist-orch")
            canonical_worktree_path(missing)

    @unittest.skipUnless(sys.platform == "win32", "drive letter case is Windows-only")
    def test_canonical_worktree_path_drive_letter_case(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td).resolve()
            raw = str(p)
            if len(raw) < 2 or raw[1] != ":":
                self.skipTest("path has no drive letter")
            flipped = raw[0].swapcase() + raw[1:]
            self.assertEqual(
                canonical_worktree_path(raw),
                canonical_worktree_path(flipped),
            )


if __name__ == "__main__":
    unittest.main()
