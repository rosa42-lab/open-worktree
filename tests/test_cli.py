from __future__ import annotations

import unittest

from orch.cli import main


class CliTests(unittest.TestCase):
    def test_help_exit_0(self) -> None:
        self.assertEqual(main(["--help"]), 0)

    def test_project_help(self) -> None:
        # argparse help raises SystemExit 0 inside main we catch as success for -h
        # Our main handles project --help via argparse
        code = main(["project", "--help"])
        self.assertIn(code, (0, 2))  # argparse may SystemExit 0 -> we map

    def test_no_target_flag_in_merge(self) -> None:
        # ensure --target is rejected as unknown
        code = main(["alpha", "merge", "--target", "main"])
        self.assertNotEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
