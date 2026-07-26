"""Agent worker subprocess protocol (V12-007).

Worker never puts secrets on argv. Control writes require matching generation.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any

from orch.agent_repo import get_run
from orch.db import open_project_db
from orch.runtime.http_client import OpenCodeHttpClient
from orch.runtime.lease import assert_write_allowed
from orch.runtime.opencode import OpenCodeRuntimeAdapter
from orch.util import utc_now_iso

HEARTBEAT_INTERVAL_SEC = 2.0


def _env(name: str, default: str | None = None) -> str:
    val = os.environ.get(name, default)
    if val is None or val == "":
        raise RuntimeError(f"missing required env {name}")
    return val


def _load_password(credential_file: str) -> tuple[str, str | None]:
    import json
    from pathlib import Path

    path = Path(credential_file)
    if not path.exists():
        return "opencode", None
    data = json.loads(path.read_text(encoding="utf-8"))
    password = data.get("password")
    if password == "":
        password = None
    return str(data.get("username") or "opencode"), password


def write_heartbeat(
    project: str,
    run_id: str,
    *,
    pid: int,
    nonce: str,
    generation: int,
) -> None:
    conn = open_project_db(project, init=True)
    try:
        row = get_run(conn, run_id)
        if row is None:
            raise RuntimeError(f"unknown run {run_id}")
        if int(row["controller_generation"]) != int(generation):
            raise RuntimeError("generation mismatch on heartbeat")
        if row.get("worker_nonce") not in (None, nonce) and row.get("worker_nonce") != nonce:
            # first heartbeat may set nonce; subsequent must match
            if row.get("heartbeat_at"):
                raise RuntimeError("worker nonce mismatch")
        now = utc_now_iso()
        conn.execute(
            """
            UPDATE agent_runs
            SET worker_pid = ?, worker_nonce = ?, heartbeat_at = ?, updated_at = ?,
                observed_state = CASE
                  WHEN observed_state IN ('exited','unreachable') THEN observed_state
                  ELSE 'running'
                END
            WHERE id = ?
            """,
            (pid, nonce, now, now, run_id),
        )
        conn.commit()
    finally:
        conn.close()


def _desired_is_running(project: str, run_id: str, generation: int) -> bool:
    conn = open_project_db(project, init=False)
    try:
        row = get_run(conn, run_id)
        if row is None:
            return False
        if int(row["controller_generation"]) != int(generation):
            return False
        return row.get("desired_state") == "running"
    finally:
        conn.close()


def worker_main(argv: list[str] | None = None) -> int:
    """
    Entry: python -m orch.runtime.worker

    Loop: first heartbeat immediately after connect, then periodic heartbeats.
    Does not auto-replay prompts. Optional ORCH_PROMPT triggers one prompt_async.
    """
    _ = argv
    run_id = _env("ORCH_RUN_ID")
    project = _env("ORCH_PROJECT")
    worktree = _env("ORCH_WORKTREE_PATH")
    server_url = _env("ORCH_SERVER_URL")
    session_id = _env("ORCH_SESSION_ID")
    generation = int(_env("ORCH_CONTROLLER_GENERATION"))
    nonce = _env("ORCH_WORKER_NONCE")
    cred_file = _env("ORCH_CREDENTIAL_FILE")
    lease_token = os.environ.get("ORCH_LEASE_TOKEN", "")
    prompt = os.environ.get("ORCH_PROMPT")

    # Safety: never operate in main/
    norm = worktree.replace("\\", "/").rstrip("/").lower()
    if norm.endswith("/main") or norm.endswith("\\main"):
        print("worker refuse: worktree is main/", file=sys.stderr)
        return 2

    username, password = _load_password(cred_file)
    client = OpenCodeHttpClient(server_url, username=username, password=password)
    adapter = OpenCodeRuntimeAdapter(client)

    # Health + session reachability before first heartbeat
    adapter.health()
    adapter.get_session(worktree, session_id)

    pid = os.getpid()
    write_heartbeat(project, run_id, pid=pid, nonce=nonce, generation=generation)

    prompted = False
    exit_code = 0
    try:
        while _desired_is_running(project, run_id, generation):
            if prompt and not prompted and lease_token:
                conn = open_project_db(project, init=False)
                try:
                    assert_write_allowed(
                        conn,
                        run_id=run_id,
                        generation=generation,
                        token=lease_token,
                        controller="agent",
                    )
                finally:
                    conn.close()
                adapter.send_prompt_async(worktree, session_id, text=prompt)
                prompted = True
                prompt = None  # never auto-replay

            write_heartbeat(
                project, run_id, pid=pid, nonce=nonce, generation=generation
            )
            time.sleep(HEARTBEAT_INTERVAL_SEC)
    except Exception as exc:  # noqa: BLE001
        exit_code = 1
        _record_exit(project, run_id, generation, exit_code, str(exc))
        return exit_code

    _record_exit(project, run_id, generation, 0, None)
    return 0


def _record_exit(
    project: str,
    run_id: str,
    generation: int,
    exit_code: int,
    error: str | None,
) -> None:
    conn = open_project_db(project, init=True)
    try:
        row = get_run(conn, run_id)
        if row is None:
            return
        if int(row["controller_generation"]) != int(generation):
            return
        now = utc_now_iso()
        # Worker does not force lifecycle to exited; lifecycle service owns that.
        # Only stamp exit evidence fields.
        conn.execute(
            """
            UPDATE agent_runs
            SET exit_code = ?, last_error = COALESCE(?, last_error),
                finished_at = ?, updated_at = ?,
                observed_state = 'exited'
            WHERE id = ?
            """,
            (exit_code, error, now, now, run_id),
        )
        conn.commit()
    finally:
        conn.close()


def spawn_worker_env(
    *,
    run_id: str,
    project: str,
    worktree_path: str,
    server_url: str,
    session_id: str,
    generation: int,
    nonce: str,
    credential_file: str,
    lease_token: str,
    prompt: str | None = None,
) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "ORCH_RUN_ID": run_id,
            "ORCH_PROJECT": project,
            "ORCH_WORKTREE_PATH": worktree_path,
            "ORCH_SERVER_URL": server_url,
            "ORCH_SESSION_ID": session_id,
            "ORCH_CONTROLLER_GENERATION": str(generation),
            "ORCH_WORKER_NONCE": nonce,
            "ORCH_CREDENTIAL_FILE": credential_file,
            "ORCH_LEASE_TOKEN": lease_token,
        }
    )
    if prompt is not None:
        env["ORCH_PROMPT"] = prompt
    # Ensure password not duplicated into unrelated vars
    return env


if __name__ == "__main__":
    raise SystemExit(worker_main(sys.argv[1:]))
