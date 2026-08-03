"""Skill consistency for orch v1.2 (supersedes exact v1.1 §16.2 byte-match)."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "orchestrator" / "SKILL.md"

REQUIRED_SNIPPETS = [
    "orch runtime start",
    "orch runtime status",
    "orch runtime stop",
    "agent-start",
    "agent-takeover",
    "agent-release",
    "agent-watch --json",
    "JSONL",
    "coordinator-bind",
    "topic-start",
    "topic-ready",
    "runtime_blocked",
    "develop",
    "1.3.0-candidate",
    "promote-develop",
    "release-sync",
    "remote-probe",
    "shell=False",
]


class SkillConsistencyTests(unittest.TestCase):
    def test_skill_covers_v12_surface(self) -> None:
        text = SKILL.read_text(encoding="utf-8")
        missing = [s for s in REQUIRED_SNIPPETS if s not in text]
        self.assertEqual(missing, [], msg=f"SKILL.md missing: {missing}")

    def test_skill_keeps_v11_merge_invariants(self) -> None:
        text = SKILL.read_text(encoding="utf-8")
        for s in (
            "enqueue",
            "merge --once",
            "retry",
            "never in `main/`",
            "lock-break --force",
        ):
            self.assertIn(s, text)


if __name__ == "__main__":
    unittest.main()
