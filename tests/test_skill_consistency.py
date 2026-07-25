"""Compare delivered SKILL.md to design §16.2 code block."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESIGN = ROOT / "worktree开发设计方案.md"
SKILL = ROOT / "skills" / "orchestrator" / "SKILL.md"


def _normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.endswith("\n"):
        text += "\n"
    return text


def extract_skill_from_design(design_text: str) -> str:
    """Extract §16.2 fenced markdown body (content has many single backticks)."""
    sec = design_text.find("### 16.2")
    if sec < 0:
        raise AssertionError("### 16.2 not found")
    end = design_text.find("### 16.3", sec)
    if end < 0:
        raise AssertionError("### 16.3 not found")
    block = design_text[sec:end]
    marker = "```markdown\n"
    start = block.find(marker)
    if start < 0:
        raise AssertionError("```markdown fence not found under §16.2")
    body = block[start + len(marker) :]
    # trailing fence before §16.3
    body = body.rstrip()
    if not body.endswith("```"):
        raise AssertionError("closing ``` not found before §16.3")
    body = body[: -len("```")].rstrip("\n") + "\n"
    return body


class SkillConsistencyTests(unittest.TestCase):
    def test_skill_matches_design_16_2(self) -> None:
        design = DESIGN.read_text(encoding="utf-8")
        expected = _normalize(extract_skill_from_design(design))
        actual = _normalize(SKILL.read_text(encoding="utf-8"))
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
