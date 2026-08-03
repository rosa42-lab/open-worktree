"""V13-005 verification_records service / topic-ready 桥接。"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from orch.db import open_project_db
from orch.errors import ValidationError
from orch.verification import repo as vrepo
from orch.verification.service import (
    create_aggregate,
    create_from_topic_ready,
    create_record,
    find_passed_for_commit,
    redact_text,
    require_passed_verification,
)
from tests.helpers.orch_env import OrchEnvTestCase


class VerificationServiceTests(OrchEnvTestCase):
    def test_topic_ready_persists_verification_record(self) -> None:
        from orch.commands.topic import coordinator_bind, topic_ready, topic_start

        coordinator_bind(
            self.project,
            session_id="ses_coord",
            directory=str(self.env.proj),
        )
        started = topic_start(
            self.project,
            name="auth",
            title="Auth",
            goal="ship auth",
            branch_name="topic/auth",
            worktree_path=str(self.env.proj / "worktrees" / "auth"),
        )
        tid = started["topic"]["id"]
        ready = topic_ready(
            self.project,
            tid,
            verification={"commit_sha": "abc123", "commands": ["pytest", "ruff"]},
        )
        self.assertIn("verification_record_id", ready)
        rid = ready["verification_record_id"]
        self.assertTrue(rid.startswith("verify_"))

        conn = open_project_db(self.project, init=True)
        try:
            rec = vrepo.get_by_id(conn, rid)
            self.assertIsNotNone(rec)
            assert rec is not None
            self.assertEqual(rec["scope"], "topic")
            self.assertEqual(rec["commit_sha"], "abc123")
            self.assertEqual(rec["status"], "passed")
            self.assertEqual(rec["commands"], ["pytest", "ruff"])
            gate = require_passed_verification(
                conn,
                self.project,
                "abc123",
                scope="topic",
                required_commands=["pytest"],
            )
            self.assertEqual(gate["id"], rid)
        finally:
            conn.close()

    def test_require_passed_fail_closed(self) -> None:
        conn = open_project_db(self.project, init=True)
        try:
            with self.assertRaises(ValidationError) as ctx:
                require_passed_verification(conn, self.project, "missing_sha")
            self.assertEqual(ctx.exception.kind, "verification_required")
        finally:
            conn.close()

    def test_aggregate_not_replaced_by_topic(self) -> None:
        conn = open_project_db(self.project, init=True)
        try:
            create_from_topic_ready(
                conn,
                project=self.project,
                topic_id="topic_1",
                commit_sha="deadbeef",
                commands=["pytest"],
            )
            conn.commit()
            # topic 记录不能当作 develop_publish 门禁
            with self.assertRaises(ValidationError):
                require_passed_verification(
                    conn,
                    self.project,
                    "deadbeef",
                    scope="develop_publish",
                    required_commands=["pytest"],
                )
            agg = create_aggregate(
                conn,
                project=self.project,
                commit_sha="deadbeef",
                commands=["pytest", "build"],
                results=[
                    {"command": "pytest", "exit_code": 0, "stdout_summary": "ok"},
                    {"command": "build", "exit_code": 0, "stdout_summary": "ok"},
                ],
                created_by="test",
            )
            conn.commit()
            found = require_passed_verification(
                conn,
                self.project,
                "deadbeef",
                scope="develop_publish",
                required_commands=["pytest", "build"],
            )
            self.assertEqual(found["id"], agg["id"])
        finally:
            conn.close()

    def test_expiry_and_supersede(self) -> None:
        conn = open_project_db(self.project, init=True)
        try:
            first = create_from_topic_ready(
                conn,
                project=self.project,
                topic_id="t1",
                commit_sha="sha1",
                commands=["pytest"],
            )
            second = create_from_topic_ready(
                conn,
                project=self.project,
                topic_id="t1",
                commit_sha="sha2",
                commands=["pytest"],
            )
            conn.commit()
            old = vrepo.get_by_id(conn, first["id"])
            assert old is not None
            self.assertEqual(old["status"], "superseded")
            self.assertEqual(vrepo.get_by_id(conn, second["id"])["status"], "passed")

            # 强制过期
            past = (
                (datetime.now(timezone.utc) - timedelta(hours=1))
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z")
            )
            conn.execute(
                "UPDATE verification_records SET expires_at = ? WHERE id = ?",
                (past, second["id"]),
            )
            conn.commit()
            self.assertIsNone(
                find_passed_for_commit(conn, self.project, "sha2", scope="topic")
            )
            refreshed = vrepo.get_by_id(conn, second["id"])
            assert refreshed is not None
            self.assertEqual(refreshed["status"], "expired")
        finally:
            conn.close()

    def test_redaction(self) -> None:
        text = redact_text("Authorization: Bearer SUPERSECRET token=abc")
        self.assertNotIn("SUPERSECRET", text)
        self.assertIn("***", text)

        conn = open_project_db(self.project, init=True)
        try:
            rec = create_record(
                conn,
                project=self.project,
                scope="develop_publish",
                commit_sha="c0ffee",
                commands=["echo"],
                results=[
                    {
                        "command": "echo",
                        "exit_code": 0,
                        "stderr_summary": "password=hunter2 leaked",
                    }
                ],
                created_by="test",
            )
            self.assertNotIn("hunter2", str(rec["results"]))
        finally:
            conn.close()
