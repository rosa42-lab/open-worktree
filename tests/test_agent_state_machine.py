"""V12-003 agent lifecycle state machine tests."""

from __future__ import annotations

import unittest

from orch.agent_state import (
    ALLOWED_LIFECYCLE,
    DESIRED_STATES,
    LIFECYCLE_STATES,
    OBSERVED_STATES,
    InvalidAgentFieldError,
    InvalidAgentTransitionError,
    assert_desired,
    assert_lifecycle_transition,
    assert_observed,
    blocks_cleanup,
)


class AgentStateMachineTests(unittest.TestCase):
    def test_happy_path(self) -> None:
        path = [
            None,
            "registered",
            "starting",
            "running",
            "pausing",
            "human_controlled",
            "resuming",
            "running",
            "stopping",
            "exited",
            "archived",
        ]
        for a, b in zip(path, path[1:]):
            assert_lifecycle_transition(a, b)

    def test_lost_reconcile_paths(self) -> None:
        for src in ("starting", "running", "pausing", "resuming", "stopping"):
            assert_lifecycle_transition(src, "lost")
        assert_lifecycle_transition("lost", "reconciling")
        for dst in ("running", "human_controlled", "exited", "manual_required"):
            assert_lifecycle_transition("reconciling", dst)

    def test_illegal_transitions(self) -> None:
        illegal = [
            (None, "running"),
            ("registered", "running"),
            ("exited", "running"),
            ("archived", "registered"),
            ("running", "registered"),
            ("human_controlled", "running"),
        ]
        for frm, to in illegal:
            with self.assertRaises(InvalidAgentTransitionError):
                assert_lifecycle_transition(frm, to)

    def test_unknown_state_rejected(self) -> None:
        with self.assertRaises(InvalidAgentTransitionError):
            assert_lifecycle_transition("running", "paused")  # paused is desired-only
        with self.assertRaises(InvalidAgentFieldError):
            assert_desired("idle")
        with self.assertRaises(InvalidAgentFieldError):
            assert_observed("registered")

    def test_closed_sets_disjoint_from_tasks(self) -> None:
        from orch.state_machine import STATUSES as TASK_STATUSES

        # lifecycle vs tasks.status must not be mixed as identical closed sets
        self.assertFalse(LIFECYCLE_STATES == TASK_STATUSES)
        self.assertTrue(LIFECYCLE_STATES.isdisjoint({"pending", "merging", "merged"}))
        self.assertIn("paused", DESIRED_STATES)
        self.assertNotIn("paused", LIFECYCLE_STATES)
        self.assertTrue(OBSERVED_STATES)

    def test_all_allowed_pairs_use_known_states(self) -> None:
        for frm, to in ALLOWED_LIFECYCLE:
            if frm is not None:
                self.assertIn(frm, LIFECYCLE_STATES)
            self.assertIn(to, LIFECYCLE_STATES)

    def test_cleanup_blocking(self) -> None:
        self.assertTrue(blocks_cleanup("running"))
        self.assertTrue(blocks_cleanup("manual_required"))
        self.assertFalse(blocks_cleanup("exited"))
        self.assertFalse(blocks_cleanup("archived"))


if __name__ == "__main__":
    unittest.main()
