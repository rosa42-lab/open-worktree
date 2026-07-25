from __future__ import annotations

import unittest

from orch.state_machine import ALLOWED, assert_transition, InvalidTransitionError


class StateMachineTests(unittest.TestCase):
    def test_allowed(self) -> None:
        for fr, to in ALLOWED:
            assert_transition(fr, to)

    def test_illegal(self) -> None:
        with self.assertRaises(InvalidTransitionError):
            assert_transition("merged", "pending")
        with self.assertRaises(InvalidTransitionError):
            assert_transition("skipped", "merging")


if __name__ == "__main__":
    unittest.main()
