"""Cross-platform exclusive file locks (task T-0108)."""

from __future__ import annotations

import json
import os
import random
import socket
import sqlite3
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from orch.audit import write_audit
from orch.constants import LOCK_WAIT_TIMEOUT_SEC
from orch.db import immediate_transaction
from orch.errors import LockError
from orch.util import utc_now_iso

AuditFn = Callable[[dict[str, Any]], None]


@dataclass
class LockHandle:
    path: Path
    token: str
    payload: dict[str, Any]


def _read_lock(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return None
        return json.loads(text)
    except (OSError, json.JSONDecodeError):
        return None


def _pid_alive(pid: int, hostname: str) -> bool | None:
    """Return True if alive, False if dead, None if indeterminate."""
    if hostname and hostname != socket.gethostname():
        return None
    if pid <= 0:
        return None
    if sys.platform == "win32":
        try:
            # tasklist locale encoding is often OEM/ANSI, not UTF-8
            completed = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                shell=False,
                capture_output=True,
                timeout=15,
                check=False,
            )
            raw = completed.stdout or b""
            out = raw.decode("utf-8", errors="replace")
            if not out.strip() or "\ufffd" in out:
                # retry with preferred encoding (e.g. cp936 on zh-CN Windows)
                import locale

                enc = locale.getpreferredencoding(False) or "gbk"
                out = raw.decode(enc, errors="replace")
            # Also try gbk explicitly for common CN installs
            if "\ufffd" in out:
                out = raw.decode("gbk", errors="replace")
            upper = out.upper()
            # No matching tasks (EN or localized)
            if (
                "NO TASKS" in upper
                or "INFO:" in upper
                or "没有运行" in out
                or "没有与" in out
                or "没有" in out and "匹配" in out
            ):
                return False
            # Header + pid line when process exists
            if str(pid) in out:
                return True
            return False
        except (OSError, subprocess.TimeoutExpired):
            return None
    # POSIX
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None


def acquire(
    lock_path: Path | str,
    *,
    command: str,
    project: str | None = None,
    timeout: float = LOCK_WAIT_TIMEOUT_SEC,
    audit_conn: sqlite3.Connection | None = None,
) -> LockHandle:
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    payload = {
        "token": token,
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
        "started_at": utc_now_iso(),
        "command": command,
        "project": project or "",
    }
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(
                str(path),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError:
            if time.monotonic() >= deadline:
                existing = _read_lock(path)
                raise LockError(
                    f"timed out waiting for lock: {path}",
                    details={
                        "lock_path": str(path),
                        "existing": existing,
                        "hint": "run orch <project> lock-status",
                    },
                )
            time.sleep(0.05 + random.random() * 0.1)
            continue
        except OSError as exc:
            raise LockError(
                f"cannot create lock: {path}: {exc}",
                details={"error": str(exc)},
            ) from exc

        try:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)

        handle = LockHandle(path=path, token=token, payload=payload)
        if audit_conn is not None and project is not None:
            try:
                with immediate_transaction(audit_conn) as conn:
                    write_audit(
                        conn,
                        "project_locked",
                        detail={
                            "token": token,
                            "pid": payload["pid"],
                            "hostname": payload["hostname"],
                            "command": command,
                            "project": project,
                        },
                    )
            except Exception:
                # If audit fails after lock acquired, still hold lock; caller may log.
                pass
        return handle


def release(
    handle: LockHandle,
    *,
    audit_conn: sqlite3.Connection | None = None,
    reason: str = "release",
) -> None:
    path = handle.path
    if not path.exists():
        return
    existing = _read_lock(path)
    if existing and existing.get("token") == handle.token:
        try:
            os.unlink(path)
        except OSError:
            return
        if audit_conn is not None:
            try:
                with immediate_transaction(audit_conn) as conn:
                    write_audit(
                        conn,
                        "project_unlocked",
                        detail={
                            "token": handle.token,
                            "pid": handle.payload.get("pid"),
                            "reason": reason,
                            "project": handle.payload.get("project"),
                        },
                    )
            except Exception:
                pass


def lock_status(lock_path: Path | str) -> dict[str, Any]:
    path = Path(lock_path)
    if not path.exists():
        return {"locked": False, "lock_path": str(path)}
    existing = _read_lock(path)
    alive: bool | None = None
    if existing and "pid" in existing:
        alive = _pid_alive(int(existing["pid"]), str(existing.get("hostname") or ""))
    return {
        "locked": True,
        "lock_path": str(path),
        "owner": existing,
        "pid_alive": alive,
    }


def lock_break(
    lock_path: Path | str,
    *,
    force: bool,
    audit_conn: sqlite3.Connection | None = None,
    project: str | None = None,
) -> dict[str, Any]:
    path = Path(lock_path)
    if not path.exists():
        return {"ok": True, "message": "no lock present", "removed": False}
    if not force:
        raise LockError(
            "refusing to break lock without --force",
            details=lock_status(path),
        )
    existing = _read_lock(path) or {}
    hostname = str(existing.get("hostname") or "")
    if hostname and hostname != socket.gethostname():
        raise LockError(
            "lock hostname does not match this host; refuse lock-break",
            details={"hostname": hostname, "local": socket.gethostname()},
        )
    pid = int(existing.get("pid") or 0)
    alive = _pid_alive(pid, hostname)
    if alive is True:
        raise LockError(
            f"lock owner pid {pid} is still alive",
            details={"pid": pid, "owner": existing},
        )
    if alive is None:
        raise LockError(
            "cannot determine pid liveness; operator inspection required",
            details={
                "pid": pid,
                "owner": existing,
                "operator_inspection": True,
            },
        )
    # alive is False
    try:
        os.unlink(path)
    except OSError as exc:
        raise LockError(f"failed to remove lock: {exc}", details={"error": str(exc)}) from exc

    if audit_conn is not None:
        try:
            with immediate_transaction(audit_conn) as conn:
                write_audit(
                    conn,
                    "project_unlocked",
                    detail={
                        "token": existing.get("token"),
                        "pid": pid,
                        "reason": "pid_dead",
                        "project": project or existing.get("project"),
                    },
                )
        except Exception:
            pass
    return {"ok": True, "removed": True, "owner": existing}
