from __future__ import annotations

import unittest

from orch.errors import (
    ALL_EXIT_CODES,
    ExitCode,
    DbError,
    GitError,
    InterruptedMergeError,
    LockError,
    PrecheckError,
    QueueBlockedError,
    UnregisteredProjectError,
    UsageError,
    ValidationError,
)


class ExitCodeTests(unittest.TestCase):
    def test_all_codes_present(self) -> None:
        expected = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 130}
        self.assertEqual(ALL_EXIT_CODES, expected)

    def test_constructors(self) -> None:
        cases = [
            UsageError("u"),
            UnregisteredProjectError("p"),
            PrecheckError("p"),
            QueueBlockedError("q"),
            LockError("l"),
            ValidationError("v"),
            GitError("g"),
            DbError("d"),
            InterruptedMergeError(),
        ]
        codes = {c.code for c in cases}
        self.assertIn(ExitCode.USAGE, codes)
        self.assertIn(ExitCode.INTERRUPTED, codes)
        self.assertIn(ExitCode.VALIDATION, codes)


if __name__ == "__main__":
    unittest.main()
