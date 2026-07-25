"""§17.10 naming; §17.11 JSON envelope; §17.14 no --target."""

from __future__ import annotations

from tests.helpers.orch_env import OrchEnvTestCase


class JsonAndNamingTests(OrchEnvTestCase):
    def test_invalid_project_name(self) -> None:
        code, payload = self.env.run_json("project", "add", "../etc", str(self.env.proj), "--json")
        self.assertEqual(code, 2)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], 2)

    def test_json_schema_fields(self) -> None:
        code, payload = self.env.run_json(self.project, "pending", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(payload["schema_version"], 1)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], f"{self.project}.pending")
        self.assertIsNone(payload["error"])
        self.assertIn("tasks", payload["data"])

    def test_unregistered_project(self) -> None:
        code, payload = self.env.run_json("nosuch", "pending", "--json")
        self.assertEqual(code, 3)
        self.assertEqual(payload["error"]["code"], 3)
