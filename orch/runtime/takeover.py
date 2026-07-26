"""Direct takeover, fork inspect, release, and open (V12-010)."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import time
import uuid
from typing import Any

from orch.agent_repo import attach_locator, get_run
from orch.agent_state import assert_lifecycle_transition
from orch.constants import project_lock_path, runtime_credentials_path
from orch.db import open_project_db
from orch.errors import ValidationError
from orch.locks import _pid_alive, acquire, release
from orch.runtime.http_client import OpenCodeHttpClient
from orch.runtime.lease import acquire_lease, assert_write_allowed, release_lease
from orch.runtime.opencode import OpenCodeRuntimeAdapter
from orch.runtime.registry import load_credentials, load_registry
from orch.runtime.worker import spawn_worker_env
from orch.util import utc_now_iso

IDLE_WAIT_SEC = 30.0
WORKER_STOP_SEC = 10.0


def _adapter() -> tuple[dict[str, Any], OpenCodeRuntimeAdapter]:
    reg = load_registry()
    if not reg or not reg.get("base_url"):
        raise ValidationError(
            "runtime Server not registered", kind="runtime_not_ready"
        )
    creds = load_credentials()
    username = "opencode"
    password = None
    if creds and creds.get("server_id") == reg.get("server_id"):
        username = str(creds.get("username") or username)
        password = creds.get("password")
        if password == "":
            password = None
    client = OpenCodeHttpClient(
        str(reg["base_url"]), username=username, password=password
    )
    return reg, OpenCodeRuntimeAdapter(client)


def _session_idle(adapter: OpenCodeRuntimeAdapter, directory: str) -> bool:
    try:
        status = adapter.get_status(directory)
    except Exception:  # noqa: BLE001
        return False
    if isinstance(status, dict):
        if status.get("busy") is True:
            return False
        # treat missing busy as idle-ish
        return True
    return False


def fork_inspect(project: str, run_id: str) -> dict[str, Any]:
    """Create inspection fork; does not change owner/generation/worker."""
    conn = open_project_db(project, init=True)
    try:
        row = get_run(conn, run_id)
        if row is None or row.get("project_name") != project:
            raise ValidationError(f"unknown run: {run_id}", kind="agent_run_not_found")
        session_id = row.get("session_id")
        wt = row.get("worktree_path")
        if not session_id or not wt:
            raise ValidationError("run missing session/worktree", kind="run_incomplete")

        reg, adapter = _adapter()
        forked = adapter.fork_session(wt, session_id)
        fork_id = f"fork_{uuid.uuid4().hex[:16]}"
        now = utc_now_iso()
        conn.execute(
            """
            INSERT INTO inspection_forks(
              id, source_run_id, session_id, worktree_path, created_at
            ) VALUES (?,?,?,?,?)
            """,
            (fork_id, run_id, forked["id"], wt, now),
        )
        conn.commit()
        locator = attach_locator(
            base_url=str(reg["base_url"]),
            worktree_path=wt,
            session_id=str(forked["id"]),
            fork=False,
        )
        # Prove owner unchanged
        after = get_run(conn, run_id)
        assert after is not None
        return {
            "mode": "fork_inspect",
            "fork": {
                "id": fork_id,
                "session_id": forked["id"],
                "worktree_path": wt,
            },
            "source_run": {
                "run_id": run_id,
                "controller": after["controller"],
                "controller_generation": after["controller_generation"],
                "worker_pid": after.get("worker_pid"),
                "state": after["state"],
            },
            "attach": locator,
            "writable_attach": False,
        }
    finally:
        conn.close()


def direct_takeover(
    project: str,
    run_id: str,
    *,
    launch: bool = False,
    idle_timeout_sec: float = IDLE_WAIT_SEC,
) -> dict[str, Any]:
    """
    generation invalidate -> worker exit -> abort -> idle -> human lease.
    Concurrent takeover seeing pausing is rejected.
    """
    lock = acquire(
        project_lock_path(project), command="agent-takeover", project=project
    )
    conn = open_project_db(project, init=True)
    human_token: str | None = None
    try:
        row = get_run(conn, run_id)
        if row is None or row.get("project_name") != project:
            raise ValidationError(f"unknown run: {run_id}", kind="agent_run_not_found")

        if row["state"] == "pausing":
            raise ValidationError(
                "another takeover is already in progress",
                kind="takeover_busy",
                details={"run_id": run_id, "state": "pausing"},
            )
        if row.get("controller") != "agent":
            raise ValidationError(
                "takeover requires controller=agent",
                kind="takeover_controller_invalid",
                details={"controller": row.get("controller")},
            )

        assert_lifecycle_transition(row["state"], "pausing")
        old_gen = int(row["controller_generation"])
        new_gen = old_gen + 1
        now = utc_now_iso()
        # Short transaction: invalidate old generation
        conn.execute(
            """
            UPDATE agent_runs
            SET state = 'pausing', desired_state = 'paused',
                controller_generation = ?, updated_at = ?
            WHERE id = ? AND state != 'pausing'
            """,
            (new_gen, now, run_id),
        )
        if conn.total_changes == 0:
            raise ValidationError(
                "concurrent takeover won the race",
                kind="takeover_busy",
            )
        conn.commit()

        # Stop worker outside DB txn
        pid = int(row.get("worker_pid") or 0)
        host = str(row.get("worker_hostname") or socket.gethostname())
        if pid > 0 and _pid_alive(pid, host) is True:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
            deadline = time.monotonic() + WORKER_STOP_SEC
            while time.monotonic() < deadline:
                if _pid_alive(pid, host) is False:
                    break
                time.sleep(0.2)
            if _pid_alive(pid, host) is True:
                try:
                    os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
                except OSError:
                    pass
            if _pid_alive(pid, host) is True:
                conn.execute(
                    """
                    UPDATE agent_runs
                    SET state = 'manual_required', updated_at = ?,
                        last_error = ?
                    WHERE id = ?
                    """,
                    (utc_now_iso(), "worker did not exit during takeover", run_id),
                )
                conn.commit()
                raise ValidationError(
                    "worker did not exit; human lease not issued",
                    kind="takeover_worker_alive",
                )

        reg, adapter = _adapter()
        session_id = str(row.get("session_id") or "")
        wt = str(row.get("worktree_path") or "")
        if session_id and wt:
            try:
                adapter.abort(wt, session_id)
            except Exception:  # noqa: BLE001
                pass

            deadline = time.monotonic() + idle_timeout_sec
            idle = False
            while time.monotonic() < deadline:
                if _session_idle(adapter, wt):
                    idle = True
                    break
                time.sleep(0.3)
            if not idle:
                conn.execute(
                    """
                    UPDATE agent_runs
                    SET state = 'manual_required', updated_at = ?,
                        last_error = ?
                    WHERE id = ?
                    """,
                    (utc_now_iso(), "session not idle before human lease", run_id),
                )
                conn.commit()
                raise ValidationError(
                    "session not idle; human lease not issued",
                    kind="takeover_session_busy",
                )

        human_token = acquire_lease(
            conn, run_id=run_id, controller="human", generation=new_gen
        )
        assert_lifecycle_transition("pausing", "human_controlled")
        conn.execute(
            """
            UPDATE agent_runs
            SET state = 'human_controlled', controller = 'human',
                desired_state = 'paused', updated_at = ?,
                worker_pid = NULL
            WHERE id = ?
            """,
            (utc_now_iso(), run_id),
        )
        conn.commit()

        locator = attach_locator(
            base_url=str(reg["base_url"]),
            worktree_path=wt,
            session_id=session_id,
        )
        launched = False
        if launch:
            launched = _launch_attach(locator["command"])

        # Return token once to human operator; never audit it.
        return {
            "mode": "direct_takeover",
            "run_id": run_id,
            "state": "human_controlled",
            "controller": "human",
            "controller_generation": new_gen,
            "lease_token": human_token,
            "attach": locator,
            "writable_attach": True,
            "launched": launched,
        }
    finally:
        conn.close()
        release(lock)


def release_control(
    project: str,
    run_id: str,
    *,
    lease_token: str,
    resume: bool = False,
    launch: bool = False,
) -> dict[str, Any]:
    lock = acquire(
        project_lock_path(project), command="agent-release", project=project
    )
    conn = open_project_db(project, init=True)
    try:
        row = get_run(conn, run_id)
        if row is None or row.get("project_name") != project:
            raise ValidationError(f"unknown run: {run_id}", kind="agent_run_not_found")
        if row.get("controller") != "human" or row.get("state") != "human_controlled":
            raise ValidationError(
                "release requires human_controlled run",
                kind="release_invalid_state",
            )
        gen = int(row["controller_generation"])
        assert_write_allowed(
            conn,
            run_id=run_id,
            generation=gen,
            token=lease_token,
            controller="human",
        )

        reg, adapter = _adapter()
        wt = str(row.get("worktree_path") or "")
        session_id = str(row.get("session_id") or "")
        if wt and not _session_idle(adapter, wt):
            raise ValidationError(
                "session busy; refuse release",
                kind="release_session_busy",
            )

        release_lease(conn, run_id=run_id)
        new_gen = gen + 1

        if not resume:
            assert_lifecycle_transition("human_controlled", "stopping")
            conn.execute(
                """
                UPDATE agent_runs
                SET state = 'stopping', desired_state = 'stopped',
                    controller = 'none', controller_generation = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (new_gen, utc_now_iso(), run_id),
            )
            conn.commit()
            assert_lifecycle_transition("stopping", "exited")
            conn.execute(
                """
                UPDATE agent_runs
                SET state = 'exited', observed_state = 'exited',
                    finished_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (utc_now_iso(), utc_now_iso(), run_id),
            )
            conn.commit()
            return {
                "mode": "release",
                "resumed": False,
                "run": get_run(conn, run_id),
            }

        # resume with new agent worker
        assert_lifecycle_transition("human_controlled", "resuming")
        import secrets
        import sys

        nonce = secrets.token_urlsafe(16)
        agent_token = acquire_lease(
            conn, run_id=run_id, controller="agent", generation=new_gen
        )
        conn.execute(
            """
            UPDATE agent_runs
            SET state = 'resuming', desired_state = 'running',
                controller = 'agent', controller_generation = ?,
                worker_nonce = ?, updated_at = ?
            WHERE id = ?
            """,
            (new_gen, nonce, utc_now_iso(), run_id),
        )
        conn.commit()

        env = spawn_worker_env(
            run_id=run_id,
            project=project,
            worktree_path=wt,
            server_url=str(reg["base_url"]),
            session_id=session_id,
            generation=new_gen,
            nonce=nonce,
            credential_file=str(runtime_credentials_path()),
            lease_token=agent_token,
            prompt=None,
        )
        proc = subprocess.Popen(
            [sys.executable, "-m", "orch.runtime.worker"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        conn.execute(
            """
            UPDATE agent_runs
            SET worker_pid = ?, worker_started_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (proc.pid, utc_now_iso(), utc_now_iso(), run_id),
        )
        conn.commit()

        # Wait briefly for heartbeat then running
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            fresh = get_run(conn, run_id)
            assert fresh is not None
            if fresh.get("heartbeat_at") and int(fresh.get("worker_pid") or 0) == proc.pid:
                assert_lifecycle_transition("resuming", "running")
                conn.execute(
                    """
                    UPDATE agent_runs
                    SET state = 'running', observed_state = 'running', updated_at = ?
                    WHERE id = ?
                    """,
                    (utc_now_iso(), run_id),
                )
                conn.commit()
                break
            if proc.poll() is not None:
                break
            time.sleep(0.25)

        locator = attach_locator(
            base_url=str(reg["base_url"]),
            worktree_path=wt,
            session_id=session_id,
        )
        launched = False
        if launch:
            launched = _launch_attach(locator["command"])
        return {
            "mode": "release",
            "resumed": True,
            "run": get_run(conn, run_id),
            "attach": locator,
            "launched": launched,
        }
    finally:
        conn.close()
        release(lock)


def agent_open(
    project: str,
    run_id: str,
    *,
    fork: bool = False,
    launch: bool = False,
) -> dict[str, Any]:
    """Return attach locator; launch only when --launch."""
    if fork:
        out = fork_inspect(project, run_id)
        if launch and out.get("attach"):
            out["launched"] = _launch_attach(out["attach"]["command"])
        else:
            out["launched"] = False
        return out

    conn = open_project_db(project, init=True)
    try:
        row = get_run(conn, run_id)
        if row is None:
            raise ValidationError(f"unknown run: {run_id}", kind="agent_run_not_found")
        reg = load_registry() or {}
        locator = attach_locator(
            base_url=str(reg.get("base_url") or "http://127.0.0.1:4096"),
            worktree_path=str(row.get("worktree_path") or ""),
            session_id=row.get("session_id"),
        )
        launched = False
        if launch:
            launched = _launch_attach(locator["command"])
        writable = row.get("controller") == "human"
        return {
            "mode": "open",
            "run_id": run_id,
            "attach": locator,
            "writable_attach": writable,
            "launched": launched,
        }
    finally:
        conn.close()


def _launch_attach(command: str) -> bool:
    # Explicit launch only. Do not use shell=True.
    import shlex
    import sys

    if sys.platform == "win32":
        # rough split preserving quotes
        args = command if isinstance(command, list) else command.split()
    else:
        args = shlex.split(command)
    try:
        subprocess.Popen(args, shell=False)  # noqa: S603
        return True
    except OSError:
        return False
