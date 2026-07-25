"""Phase 0 smoke tests."""

from __future__ import annotations

import unittest

from tests.helpers.git_fixture import make_bare_with_develop


class SmokeTests(unittest.TestCase):
    def test_import_orch(self) -> None:
        import orch

        self.assertTrue(orch.__version__)

    def test_constants_develop(self) -> None:
        from orch.constants import TARGET_BRANCH

        self.assertEqual(TARGET_BRANCH, "develop")

    def test_fixture_bare_develop(self) -> None:
        root = make_bare_with_develop()
        bare = root / ".bare.git"
        self.assertTrue(bare.is_dir())


if __name__ == "__main__":
    unittest.main()
