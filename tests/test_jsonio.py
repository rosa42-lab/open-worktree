from __future__ import annotations

import json
import unittest

from orch.constants import JSON_SCHEMA_VERSION
from orch.errors import ValidationError
from orch.jsonio import envelope_from_exception, success_envelope


class JsonioTests(unittest.TestCase):
    def test_success(self) -> None:
        env = success_envelope("alpha.pending", {"tasks": []})
        self.assertEqual(env["schema_version"], JSON_SCHEMA_VERSION)
        self.assertTrue(env["ok"])
        self.assertIsNone(env["error"])

    def test_error(self) -> None:
        env = envelope_from_exception(
            "alpha.enqueue", ValidationError("worktree is not clean")
        )
        self.assertFalse(env["ok"])
        self.assertEqual(env["error"]["code"], 7)
        self.assertEqual(env["error"]["kind"], "enqueue_validation_failed")
        # serializable
        json.dumps(env)


if __name__ == "__main__":
    unittest.main()
