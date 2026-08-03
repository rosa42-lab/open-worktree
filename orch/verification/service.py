"""verification 领域服务：创建、聚合、过期、promotion 门禁查询。"""

from __future__ import annotations

import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from orch.errors import ValidationError
from orch.util import utc_now_iso
from orch.verification import repo as vrepo

SCOPES = frozenset(
    {"topic", "develop_publish", "candidate_publish", "master_release"}
)
DEFAULT_TTL_HOURS = 72
_BEARER_RE = re.compile(r"(?i)bearer\s+\S+")
_KV_SECRET_RE = re.compile(
    r"(?i)(token|password|authorization|api[_-]?key|secret|credential)\s*[:=]\s*\S+"
)


def _parse_iso(ts: str) -> datetime:
    text = ts.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def redact_text(text: str, *, max_len: int = 400) -> str:
    if not text:
        return ""
    redacted = _BEARER_RE.sub("Bearer ***", text)
    redacted = _KV_SECRET_RE.sub(r"\1=***", redacted)
    return redacted[:max_len]


def redact_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in results:
        row = dict(item)
        for key in ("stdout_summary", "stderr_summary", "detail", "evidence_path"):
            if key in row and isinstance(row[key], str):
                row[key] = redact_text(row[key])
        # 禁止完整敏感字段
        for banned in ("authorization", "token", "password", "raw_log"):
            row.pop(banned, None)
        out.append(row)
    return out


def _new_id() -> str:
    return f"verify_{uuid.uuid4().hex[:16]}"


def create_record(
    conn: sqlite3.Connection,
    *,
    project: str,
    scope: str,
    commit_sha: str,
    commands: list[str],
    results: list[dict[str, Any]],
    created_by: str,
    topic_id: str | None = None,
    status: str = "passed",
    ttl_hours: int = DEFAULT_TTL_HOURS,
    started_at: str | None = None,
    finished_at: str | None = None,
) -> dict[str, Any]:
    if scope not in SCOPES:
        raise ValidationError(
            f"invalid verification scope: {scope}",
            kind="verification_invalid",
            details={"scope": scope},
        )
    if not commit_sha or not isinstance(commit_sha, str):
        raise ValidationError(
            "commit_sha is required",
            kind="verification_invalid",
        )
    if not commands or not all(isinstance(c, str) and c.strip() for c in commands):
        raise ValidationError(
            "commands must be a non-empty list of strings",
            kind="verification_invalid",
        )
    if status == "passed":
        _assert_results_complete(commands, results)

    now = utc_now_iso()
    started = started_at or now
    finished = finished_at if finished_at is not None else (now if status != "running" else None)
    expires = (
        (_parse_iso(started) + timedelta(hours=ttl_hours))
        .astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    record = {
        "id": _new_id(),
        "project_name": project,
        "scope": scope,
        "commit_sha": commit_sha.strip(),
        "status": status,
        "commands": list(commands),
        "results": redact_results(results),
        "created_by": created_by,
        "started_at": started,
        "finished_at": finished,
        "expires_at": expires,
        "topic_id": topic_id,
        "created_at": now,
    }
    if topic_id and scope == "topic":
        vrepo.supersede_topic_records(conn, project, topic_id)
    vrepo.insert_record(conn, record)
    return record


def create_from_topic_ready(
    conn: sqlite3.Connection,
    *,
    project: str,
    topic_id: str,
    commit_sha: str,
    commands: list[str],
    created_by: str = "topic-ready",
    results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    将 topic-ready 的自由格式 verification 桥接为 commit-bound record。
    若未提供 results，按“声明成功”写入脱敏摘要（保持既有信任模型，但可查询）。
    """
    if results is None:
        results = [
            {
                "command": cmd,
                "exit_code": 0,
                "stdout_summary": "",
                "stderr_summary": "",
                "detail": "declared_by_topic_ready",
            }
            for cmd in commands
        ]
    return create_record(
        conn,
        project=project,
        scope="topic",
        commit_sha=commit_sha,
        commands=commands,
        results=results,
        created_by=created_by,
        topic_id=topic_id,
        status="passed",
    )


def create_aggregate(
    conn: sqlite3.Connection,
    *,
    project: str,
    commit_sha: str,
    commands: list[str],
    results: list[dict[str, Any]],
    created_by: str,
    scope: str = "develop_publish",
) -> dict[str, Any]:
    """
    聚合门禁：必须在最终 source_sha 上重新提供 required commands 的结果。
    逐 topic 记录只能作 provenance，不能替代本记录。
    """
    if scope not in ("develop_publish", "candidate_publish", "master_release"):
        raise ValidationError(
            "aggregate scope must be develop_publish|candidate_publish|master_release",
            kind="verification_invalid",
            details={"scope": scope},
        )
    return create_record(
        conn,
        project=project,
        scope=scope,
        commit_sha=commit_sha,
        commands=commands,
        results=results,
        created_by=created_by,
        topic_id=None,
        status="passed",
    )


def _assert_results_complete(
    commands: list[str],
    results: list[dict[str, Any]],
) -> None:
    if not results:
        raise ValidationError(
            "results required for passed verification",
            kind="verification_incomplete",
        )
    by_cmd = {str(r.get("command")): r for r in results if isinstance(r, dict)}
    missing = [c for c in commands if c not in by_cmd]
    if missing:
        raise ValidationError(
            f"results missing for commands: {missing}",
            kind="verification_incomplete",
            details={"missing": missing},
        )
    for cmd in commands:
        row = by_cmd[cmd]
        if "exit_code" not in row:
            raise ValidationError(
                f"exit_code missing for command: {cmd}",
                kind="verification_incomplete",
            )
        if row.get("exit_code") != 0:
            raise ValidationError(
                f"command failed: {cmd}",
                kind="verification_failed",
                details={"command": cmd, "exit_code": row.get("exit_code")},
            )


def refresh_expiry(conn: sqlite3.Connection, record: dict[str, Any]) -> dict[str, Any]:
    """若已过期则标记 expired 并返回更新后的记录。"""
    if record["status"] != "passed":
        return record
    expires = record.get("expires_at")
    if not expires:
        return record
    now = datetime.now(timezone.utc)
    if _parse_iso(expires) <= now:
        vrepo.update_status(conn, record["id"], "expired", finished_at=utc_now_iso())
        record = dict(record)
        record["status"] = "expired"
    return record


def find_passed_for_commit(
    conn: sqlite3.Connection,
    project: str,
    commit_sha: str,
    *,
    scope: str | None = None,
    required_commands: list[str] | None = None,
) -> dict[str, Any] | None:
    """返回第一条仍有效的 passed 记录（自动过期刷新）。"""
    records = vrepo.list_by_commit(conn, project, commit_sha, scope=scope)
    for rec in records:
        rec = refresh_expiry(conn, rec)
        if rec["status"] != "passed":
            continue
        if required_commands:
            have = set(rec.get("commands") or [])
            if not set(required_commands).issubset(have):
                continue
            # 结果完整性
            try:
                _assert_results_complete(list(required_commands), list(rec.get("results") or []))
            except ValidationError:
                continue
        return rec
    return None


def require_passed_verification(
    conn: sqlite3.Connection,
    project: str,
    commit_sha: str,
    *,
    scope: str | None = None,
    required_commands: list[str] | None = None,
) -> dict[str, Any]:
    """
    Promotion 门禁：必须有独立 passed/未过期/命令完整的 record。
    不接受临时 JSON；找不到则 fail-closed。
    """
    rec = find_passed_for_commit(
        conn,
        project,
        commit_sha,
        scope=scope,
        required_commands=required_commands,
    )
    if rec is None:
        raise ValidationError(
            "no passed verification_record for commit; promotion fail-closed",
            kind="verification_required",
            details={
                "project": project,
                "commit_sha": commit_sha,
                "scope": scope,
                "required_commands": required_commands,
            },
        )
    return rec
