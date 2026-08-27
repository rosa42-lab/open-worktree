"""V17 CLI contract: help lock table, doctor, topic metavar (argv)."""

from __future__ import annotations

import argparse
import io
import unittest
from contextlib import redirect_stdout

from orch.cli import PROJECT_COMMANDS, _parser_for_project, lock_for, main


def _help_text(*argv: str) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = main(list(argv))
    return code, buf.getvalue()


def _subparser_names(parser: argparse.ArgumentParser) -> frozenset[str]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return frozenset(action.choices)
    return frozenset()


class CliContractV17Tests(unittest.TestCase):
    def test_project_help_lists_every_command(self) -> None:
        code, text = _help_text("alpha", "--help")
        self.assertEqual(code, 0)
        missing = [name for name in sorted(PROJECT_COMMANDS) if name not in text]
        self.assertEqual(missing, [], msg=text)

    def test_parser_names_match_project_commands(self) -> None:
        names = _subparser_names(_parser_for_project("alpha"))
        self.assertEqual(names, PROJECT_COMMANDS)

    def test_doctor_is_a_project_command(self) -> None:
        self.assertIn("doctor", PROJECT_COMMANDS)
        code, text = _help_text("alpha", "--help")
        self.assertEqual(code, 0)
        self.assertIn("doctor", text)

    def test_topic_start_help_separates_agent_and_session(self) -> None:
        code, text = _help_text("alpha", "topic-start", "--help")
        self.assertEqual(code, 0)
        self.assertIn("--agent", text)
        self.assertIn("--start-session", text)
        agent_lines = [
            ln for ln in text.splitlines() if "--agent" in ln and "--start-session" not in ln
        ]
        blob = "\n".join(agent_lines).lower()
        self.assertNotIn("start an opencode session", blob)

    def test_topic_ref_metavar(self) -> None:
        for cmd in (
            "topic-show",
            "topic-open",
            "topic-ready",
            "topic-enqueue",
            "topic-abandon",
            "topic-archive",
        ):
            code, text = _help_text("alpha", cmd, "--help")
            self.assertEqual(code, 0, msg=cmd)
            self.assertIn("TOPIC_ID_OR_NAME", text, msg=cmd)

    def test_lock_for_readonly_and_flag_locks(self) -> None:
        parser = _parser_for_project("alpha")
        self.assertIsNone(lock_for(parser.parse_args(["list"])))
        self.assertIsNone(lock_for(parser.parse_args(["topic-show", "t"])))
        self.assertIsNone(lock_for(parser.parse_args(["doctor"])))
        self.assertIsNone(lock_for(parser.parse_args(["cleanup"])))
        self.assertEqual(lock_for(parser.parse_args(["cleanup", "--prune"])), "project")
        self.assertIsNone(lock_for(parser.parse_args(["topic-open", "t"])))
        self.assertEqual(
            lock_for(parser.parse_args(["topic-open", "t", "--fork"])), "project"
        )
        self.assertIsNone(lock_for(parser.parse_args(["agent-open", "run_1"])))
        self.assertEqual(
            lock_for(parser.parse_args(["agent-open", "run_1", "--fork"])),
            "project",
        )
        self.assertEqual(
            lock_for(
                parser.parse_args(
                    [
                        "topic-start",
                        "n",
                        "--title",
                        "t",
                        "--goal",
                        "g",
                        "--branch",
                        "feat/x",
                    ]
                )
            ),
            "project",
        )

    def test_topic_start_help_has_brief_file(self) -> None:
        code, text = _help_text("alpha", "topic-start", "--help")
        self.assertEqual(code, 0)
        self.assertIn("--brief-file", text)


if __name__ == "__main__":
    unittest.main()
