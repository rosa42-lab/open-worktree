from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from orch.db import connect, immediate_transaction, init_schema


class DbTests(unittest.TestCase):
    def test_schema_and_counter(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "t.db"
            conn = connect(db)
            init_schema(conn)
            with immediate_transaction(conn) as c:
                c.execute(
                    "UPDATE counters SET value = value + 1 WHERE name = 'queue_seq'"
                )
                v = c.execute(
                    "SELECT value FROM counters WHERE name = 'queue_seq'"
                ).fetchone()["value"]
            self.assertEqual(v, 1)
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            self.assertIn("tasks", tables)
            self.assertIn("audit_log", tables)
            conn.close()


if __name__ == "__main__":
    unittest.main()
