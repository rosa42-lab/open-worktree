"""Observe-only agent commands (V12-006). No project lock, no control requests."""

from __future__ import annotations

import json
import sys
import time
from typing import Any, TextIO

from orch.agent_repo import (
    attach_locator,
    get_run,
    list_runs,
    register_observed_run,
)
from orch.db import open_project_db
from orch.errors import ValidationError
from orch.runtime.http_client import OpenCodeHttpClient
from orch.runtime.opencode import OpenCodeRuntimeAdapter

WATCH_STREAM_SCHEMA = 1


def _run_summary(row: dict[str, Any], *, base_url: str | None = None) -> dict[str, Any]:
    locator = None
    if base_url and row.get("session_id") and row.get("worktree_path"):
        locator = attach_locator(
            base_url=base_url,
            worktree_path=row["worktree_path"],
            session_id=row["session_id"],
        )
    return {
        "run_id": row["id"],
        "agent_name": row["agent_name"],
        "branch_name": row["branch_name"],
        "worktree_path": row["worktree_path"],
        "session_id": row.get("session_id"),
        "state": row["state"],
        "desired_state": row["desired_state"],
        "observed_state": row["observed_state"],
        "controller": row["controller"],
        "controller_generation": row["controller_generation"],
        "worker_pid": row.get("worker_pid"),
        "runtime_server_id": row.get("runtime_server_id"),
        "heartbeat_at": row.get("heartbeat_at"),
        "last_error": row.get("last_error"),
        "attach": locator,
    }


def cmd_agent_list(
    project: str,
    *,
    all_runs: bool = False,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Read-only list of agent runs. Does not acquire project lock."""
    conn = open_project_db(project, init=True)
    try:
        rows = list_runs(conn, project, include_archived=all_runs)
        return {
            "project": project,
            "runs": [_run_summary(r, base_url=base_url) for r in rows],
        }
    finally:
        conn.close()


def cmd_agent_show(
    project: str,
    run_id: str,
    *,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Read-only show of one agent run. Does not acquire project lock."""
    conn = open_project_db(project, init=True)
    try:
        row = get_run(conn, run_id)
        if row is None or row.get("project_name") != project:
            raise ValidationError(
                f"unknown run: {run_id}",
                kind="agent_run_not_found",
                details={"run_id": run_id, "project": project},
            )
        return {"project": project, "run": _run_summary(row, base_url=base_url)}
    finally:
        conn.close()


def cmd_agent_register(
    project: str,
    *,
    agent: str,
    branch: str,
    worktree_path: str,
    session_id: str,
    runtime_server_id: str = "external",
    base_url: str | None = None,
) -> dict[str, Any]:
    """
    Temporary fixture command: map an existing worktree/session into agent_runs.

    Writes DB only; does not start/control worker or send OpenCode control requests.
    """
    conn = open_project_db(project, init=True)
    try:
        row = register_observed_run(
            conn,
            project_name=project,
            agent_name=agent,
            branch_name=branch,
            worktree_path=worktree_path,
            session_id=session_id,
            runtime_server_id=runtime_server_id,
        )
        return {
            "project": project,
            "registered": True,
            "run": _run_summary(row, base_url=base_url),
        }
    finally:
        conn.close()


def cmd_agent_watch(
    project: str,
    run_id: str,
    *,
    base_url: str | None = None,
    interval_sec: float = 1.0,
    max_ticks: int = 1,
    as_jsonl: bool = False,
    stream: TextIO | None = None,
    adapter: OpenCodeRuntimeAdapter | None = None,
) -> dict[str, Any] | None:
    """
    Observe-only watch. Never sends abort/prompt/dispose/signal.

    --json / as_jsonl: emit JSONL stream (header + ticks), return None.
    Otherwise return a single snapshot dict for the v1.1 JSON envelope.
    """
    conn = open_project_db(project, init=True)
    try:
        row = get_run(conn, run_id)
        if row is None or row.get("project_name") != project:
            raise ValidationError(
                f"unknown run: {run_id}",
                kind="agent_run_not_found",
                details={"run_id": run_id, "project": project},
            )

        out = stream or sys.stdout
        if as_jsonl:
            header = {
                "type": "stream_header",
                "stream": "agent-watch",
                "schema_version": WATCH_STREAM_SCHEMA,
                "project": project,
                "run_id": run_id,
            }
            out.write(json.dumps(header, ensure_ascii=False) + "\n")
            out.flush()

        last_payload: dict[str, Any] | None = None
        ticks = max(1, int(max_ticks))
        for i in range(ticks):
            # Re-read DB each tick (observe-only; no writes).
            fresh = get_run(conn, run_id)
            assert fresh is not None
            session_status = None
            if adapter is not None and fresh.get("session_id") and fresh.get("worktree_path"):
                try:
                    session_status = adapter.get_status(
                        fresh["worktree_path"], fresh["session_id"]
                    )
                except Exception as exc:  # noqa: BLE001
                    session_status = {"error": str(exc)}

            payload = {
                "type": "tick",
                "seq": i,
                "run": _run_summary(fresh, base_url=base_url),
                "session_status": session_status,
            }
            last_payload = payload
            if as_jsonl:
                out.write(json.dumps(payload, ensure_ascii=False) + "\n")
                out.flush()
                if i + 1 < ticks:
                    time.sleep(max(0.0, interval_sec))

        if as_jsonl:
            footer = {
                "type": "stream_footer",
                "stream": "agent-watch",
                "schema_version": WATCH_STREAM_SCHEMA,
                "ticks": ticks,
            }
            out.write(json.dumps(footer, ensure_ascii=False) + "\n")
            out.flush()
            return None

        return {
            "project": project,
            "run_id": run_id,
            "snapshot": last_payload,
        }
    finally:
        conn.close()


def build_observe_adapter(
    base_url: str,
    *,
    username: str | None = None,
    password: str | None = None,
) -> OpenCodeRuntimeAdapter:
    """Factory for optional live status reads (GET only)."""
    client = OpenCodeHttpClient(
        base_url, username=username, password=password
    )
    return OpenCodeRuntimeAdapter(client)
