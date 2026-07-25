from __future__ import annotations

import unittest

from orch.errors import UsageError
from orch.validate import (
    branch_safe_name,
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


if __name__ == "__main__":
    unittest.main()
