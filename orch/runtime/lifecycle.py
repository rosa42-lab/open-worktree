"""AgentLifecycleService (V12-008) — sole owner of run start/stop/reconcile/archive."""

from __future__ import annotations

import os
import secrets
import signal
import subprocess
import sys
import time
from typing import Any

from orch.agent_repo import get_run, list_runs, new_run_id
from orch.agent_state import (
    CLEANUP_BLOCKING_LIFECYCLE,
    assert_lifecycle_transition,
)
from orch.constants import project_lock_path, runtime_credentials_path
from orch.db import open_project_db
from orch.errors import ValidationError
from orch.locks import _pid_alive, acquire, release
from orch.runtime.http_client import OpenCodeHttpClient
from orch.runtime.lease import acquire_lease, release_lease
from orch.runtime.opencode import OpenCodeRuntimeAdapter
from orch.runtime.registry import load_credentials, load_registry, public_registry_view
from orch.runtime.worker import spawn_worker_env
from orch.util import utc_now_iso

STARTUP_HEARTBEAT_DEADLINE_SEC = 15.0


class AgentLifecycleService:
    def __init__(self, project: str) -> None:
        self.project = project

    def start(
        self,
        *,
        agent: str,
        branch: str,
        worktree_path: str,
        prompt: str | None = None,
        session_id: str | None = None,
        create_session: bool = True,
    ) -> dict[str, Any]:
        """
        Precheck -> Register -> Start worker -> Finalize on first heartbeat.
        """
        lock = acquire(
            project_lock_path(self.project),
            command="agent-start",
            project=self.project,
        )
        conn = open_project_db(self.project, init=True)
        try:
            reg = load_registry()
            if not reg or not reg.get("base_url"):
                raise ValidationError(
                    "runtime Server not registered; run: orch runtime start",
                    kind="runtime_not_ready",
                )
            creds = load_credentials()
            if not creds or creds.get("server_id") != reg.get("server_id"):
                raise ValidationError(
                    "runtime credentials missing or mismatched",
                    kind="runtime_credentials_missing",
                )

            # Precheck: no other active run on worktree
            for row in list_runs(conn, self.project):
                if (
                    row.get("worktree_path") == worktree_path
                    and row.get("state") in CLEANUP_BLOCKING_LIFECYCLE
                ):
                    raise ValidationError(
                        f"worktree already owned by active run {row['id']}",
                        kind="agent_worktree_busy",
                        details={"run_id": row["id"]},
                    )

            username = str(creds.get("username") or "opencode")
            password = creds.get("password")
            if password == "":
                password = None
            client = OpenCodeHttpClient(
                str(reg["base_url"]), username=username, password=password
            )
            adapter = OpenCodeRuntimeAdapter(client)

            if session_id is None and create_session:
                sess = adapter.create_session(
                    worktree_path, title=f"{agent}:{branch}"
                )
                session_id = str(sess["id"])
            if not session_id:
                raise ValidationError("session_id required", kind="session_required")

            run_id = new_run_id()
            now = utc_now_iso()
            generation = 1
            nonce = secrets.token_urlsafe(16)
            conn.execute(
                """
                INSERT INTO agent_runs (
                  id, project_name, agent_name, branch_name, worktree_path,
                  task_id, runtime_kind, runtime_server_id, session_id,
                  state, desired_state, observed_state, controller,
                  controller_generation, worker_nonce, created_at, updated_at,
                  started_at
                ) VALUES (?,?,?,?,?, NULL,?,?,?, 'registered','running','starting','agent',
                  ?,?,?,?,?)
                """,
                (
                    run_id,
                    self.project,
                    agent,
                    branch,
                    worktree_path,
                    "opencode",
                    str(reg["server_id"]),
                    session_id,
                    generation,
                    nonce,
                    now,
                    now,
                    now,
                ),
            )
            conn.execute(
                "INSERT INTO lifecycle_counters(run_id, value) VALUES (?, 0)",
                (run_id,),
            )
            conn.commit()

            # registered -> starting
            self._set_state(conn, run_id, "starting")
            token = acquire_lease(
                conn, run_id=run_id, controller="agent", generation=generation
            )

            env = spawn_worker_env(
                run_id=run_id,
                project=self.project,
                worktree_path=worktree_path,
                server_url=str(reg["base_url"]),
                session_id=session_id,
                generation=generation,
                nonce=nonce,
                credential_file=str(runtime_credentials_path()),
                lease_token=token,
                prompt=prompt,
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

            # Finalize: wait for first heartbeat matching pid/nonce/generation
            deadline = time.monotonic() + STARTUP_HEARTBEAT_DEADLINE_SEC
            finalized = False
            while time.monotonic() < deadline:
                row = get_run(conn, run_id)
                assert row is not None
                if (
                    row.get("heartbeat_at")
                    and int(row.get("worker_pid") or 0) == int(proc.pid or 0)
                    and row.get("worker_nonce") == nonce
                    and int(row.get("controller_generation") or 0) == generation
                ):
                    # session reachable check
                    try:
                        adapter.get_session(worktree_path, session_id)
                        self._set_state(conn, run_id, "running")
                        conn.execute(
                            """
                            UPDATE agent_runs
                            SET observed_state = 'running', updated_at = ?
                            WHERE id = ?
                            """,
                            (utc_now_iso(), run_id),
                        )
                        conn.commit()
                        finalized = True
                        break
                    except Exception:  # noqa: BLE001
                        pass
                if proc.poll() is not None:
                    break
                time.sleep(0.25)

            row = get_run(conn, run_id)
            assert row is not None
            if not finalized:
                # Keep starting -> lost/reconciling path; never mark running
                self._set_state(conn, run_id, "lost")
                return {
                    "run": self._public_run(row),
                    "finalized": False,
                    "warning": "first heartbeat not observed before deadline",
                    "registry": public_registry_view(reg),
                }

            row = get_run(conn, run_id)
            return {
                "run": self._public_run(row),
                "finalized": True,
                "registry": public_registry_view(reg),
            }
        finally:
            conn.close()
            release(lock)

    def stop(self, run_id: str, *, force_after_sec: float = 5.0) -> dict[str, Any]:
        lock = acquire(
            project_lock_path(self.project),
            command="agent-stop",
            project=self.project,
        )
        conn = open_project_db(self.project, init=True)
        try:
            row = get_run(conn, run_id)
            if row is None or row.get("project_name") != self.project:
                raise ValidationError(
                    f"unknown run: {run_id}", kind="agent_run_not_found"
                )
            # desired stopped + bump generation to invalidate worker
            gen = int(row["controller_generation"]) + 1
            now = utc_now_iso()
            if row["state"] not in ("stopping", "exited", "archived"):
                assert_lifecycle_transition(row["state"], "stopping")
            conn.execute(
                """
                UPDATE agent_runs
                SET desired_state = 'stopped', state = 'stopping',
                    controller_generation = ?, updated_at = ?
                WHERE id = ?
                """,
                (gen, now, run_id),
            )
            conn.commit()
            release_lease(conn, run_id=run_id)

            pid = int(row.get("worker_pid") or 0)
            host = str(row.get("worker_hostname") or "") or __import__("socket").gethostname()
            if pid > 0 and _pid_alive(pid, host) is True:
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
                deadline = time.monotonic() + force_after_sec
                while time.monotonic() < deadline:
                    if _pid_alive(pid, host) is False:
                        break
                    time.sleep(0.2)
                if _pid_alive(pid, host) is True:
                    try:
                        os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
                    except OSError:
                        pass

            self._set_state(conn, run_id, "exited")
            conn.execute(
                """
                UPDATE agent_runs
                SET observed_state = 'exited', finished_at = ?, updated_at = ?,
                    controller = 'none'
                WHERE id = ?
                """,
                (utc_now_iso(), utc_now_iso(), run_id),
            )
            conn.commit()
            return {"run": self._public_run(get_run(conn, run_id))}
        finally:
            conn.close()
            release(lock)

    def reconcile(self, run_id: str | None = None) -> dict[str, Any]:
        lock = acquire(
            project_lock_path(self.project),
            command="agent-reconcile",
            project=self.project,
        )
        conn = open_project_db(self.project, init=True)
        try:
            targets = []
            if run_id:
                row = get_run(conn, run_id)
                if row is None:
                    raise ValidationError(
                        f"unknown run: {run_id}", kind="agent_run_not_found"
                    )
                targets = [row]
            else:
                targets = [
                    r
                    for r in list_runs(conn, self.project)
                    if r.get("state") in ("lost", "reconciling", "starting", "running")
                ]

            results = []
            for row in targets:
                results.append(self._reconcile_one(conn, row))
            return {"reconciled": results}
        finally:
            conn.close()
            release(lock)

    def archive(self, run_id: str) -> dict[str, Any]:
        lock = acquire(
            project_lock_path(self.project),
            command="agent-archive",
            project=self.project,
        )
        conn = open_project_db(self.project, init=True)
        try:
            row = get_run(conn, run_id)
            if row is None:
                raise ValidationError(
                    f"unknown run: {run_id}", kind="agent_run_not_found"
                )
            assert_lifecycle_transition(row["state"], "archived")
            now = utc_now_iso()
            conn.execute(
                """
                UPDATE agent_runs
                SET state = 'archived', archived_at = ?, updated_at = ?,
                    controller = 'none'
                WHERE id = ?
                """,
                (now, now, run_id),
            )
            conn.commit()
            release_lease(conn, run_id=run_id)
            return {"run": self._public_run(get_run(conn, run_id))}
        finally:
            conn.close()
            release(lock)

    def _reconcile_one(self, conn: Any, row: dict[str, Any]) -> dict[str, Any]:
        run_id = row["id"]
        pid = int(row.get("worker_pid") or 0)
        host = str(row.get("worker_hostname") or "") or __import__("socket").gethostname()
        alive = _pid_alive(pid, host) if pid else False
        hb = row.get("heartbeat_at")

        if row["state"] == "running" and alive is False:
            if row["state"] != "lost":
                try:
                    assert_lifecycle_transition(row["state"], "lost")
                    self._set_state(conn, run_id, "lost")
                except Exception:  # noqa: BLE001
                    pass
            return {"run_id": run_id, "action": "mark_lost", "worker_alive": alive}

        if row["state"] in ("lost", "starting") and alive is False:
            try:
                assert_lifecycle_transition(
                    get_run(conn, run_id)["state"], "reconciling"  # type: ignore[index]
                )
                self._set_state(conn, run_id, "reconciling")
                # Without prompt receipt proof -> manual_required
                self._set_state(conn, run_id, "manual_required")
                return {
                    "run_id": run_id,
                    "action": "manual_required",
                    "reason": "insufficient prompt/session evidence to replay",
                    "heartbeat_at": hb,
                }
            except Exception as exc:  # noqa: BLE001
                return {"run_id": run_id, "action": "noop", "error": str(exc)}

        return {"run_id": run_id, "action": "noop", "worker_alive": alive}

    def _set_state(self, conn: Any, run_id: str, to_state: str) -> None:
        row = get_run(conn, run_id)
        assert row is not None
        assert_lifecycle_transition(row["state"], to_state)
        conn.execute(
            "UPDATE agent_runs SET state = ?, updated_at = ? WHERE id = ?",
            (to_state, utc_now_iso(), run_id),
        )
        conn.commit()

    @staticmethod
    def _public_run(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
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
            "heartbeat_at": row.get("heartbeat_at"),
            "last_error": row.get("last_error"),
        }
