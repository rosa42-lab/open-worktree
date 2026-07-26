"""Host-level OpenCode Server registry (V12-004). Secrets never in argv/JSON public views."""

from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import uuid
from pathlib import Path
from typing import Any

from orch.constants import (
    DEFAULT_RUNTIME_HOST,
    DEFAULT_RUNTIME_PORT,
    DEFAULT_RUNTIME_USERNAME,
    runtime_credentials_path,
    runtime_dir,
    runtime_lock_path,
    runtime_log_dir,
    runtime_log_path,
    runtime_registry_path,
)
from orch.errors import ExitCode, OrchError
from orch.locks import _pid_alive  # internal reuse for identity checks
from orch.util import utc_now_iso

REGISTRY_SCHEMA_VERSION = 1


class RuntimeRegistryError(OrchError):
    def __init__(
        self,
        message: str,
        *,
        kind: str = "runtime_registry_error",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message, code=ExitCode.GENERAL, kind=kind, details=details
        )


def ensure_runtime_dirs() -> None:
    runtime_dir().mkdir(parents=True, exist_ok=True)
    runtime_log_dir().mkdir(parents=True, exist_ok=True)


def _atomic_write_json(path: Path, payload: dict[str, Any], *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    data = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    fd = os.open(str(tmp), os.O_CREAT | os.O_TRUNC | os.O_WRONLY, mode)
    try:
        os.write(fd, data.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(str(tmp), str(path))
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def load_registry() -> dict[str, Any] | None:
    path = runtime_registry_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeRegistryError(
            f"cannot read runtime registry: {exc}",
            details={"path": str(path)},
        ) from exc
    if not isinstance(data, dict):
        raise RuntimeRegistryError("runtime registry is not an object")
    return data


def save_registry(payload: dict[str, Any]) -> None:
    ensure_runtime_dirs()
    _atomic_write_json(runtime_registry_path(), payload, mode=0o644)


def load_credentials() -> dict[str, Any] | None:
    path = runtime_credentials_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeRegistryError(
            f"cannot read runtime credentials: {exc}",
            details={"path": str(path)},
        ) from exc
    if not isinstance(data, dict):
        raise RuntimeRegistryError("runtime credentials is not an object")
    return data


def save_credentials(
    *,
    username: str,
    password: str,
    server_id: str,
) -> Path:
    """
    Write credentials with restrictive mode when OS supports it.

    H10 note: on Windows, file ACLs are same-user readable by default; this is
    not a security boundary. Password never goes into argv.
    """
    ensure_runtime_dirs()
    path = runtime_credentials_path()
    payload = {
        "schema_version": 1,
        "server_id": server_id,
        "username": username,
        "password": password,
        "updated_at": utc_now_iso(),
        "exposure_note": (
            "same-user readable; not a cross-account secret store "
            "(Windows ACL / POSIX mode best-effort only)"
        ),
    }
    _atomic_write_json(path, payload, mode=0o600)
    return path


def public_registry_view(reg: dict[str, Any] | None) -> dict[str, Any] | None:
    """Strip anything that could leak secrets (credentials are separate file)."""
    if reg is None:
        return None
    out = dict(reg)
    for key in ("password", "token", "authorization", "secret"):
        out.pop(key, None)
    return out


def new_server_id() -> str:
    return f"srv_{uuid.uuid4().hex[:16]}"


def new_server_nonce() -> str:
    return secrets.token_urlsafe(24)


def build_registry_record(
    *,
    server_id: str,
    base_url: str,
    pid: int,
    server_generation: int,
    server_nonce: str,
    managed_by_orch: bool,
    mode: str = "shared",
    capabilities: dict[str, bool] | None = None,
    hostname: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "server_id": server_id,
        "mode": mode,
        "base_url": base_url,
        "pid": pid,
        "hostname": hostname or socket.gethostname(),
        "server_generation": server_generation,
        "server_nonce": server_nonce,
        "started_at": utc_now_iso(),
        "managed_by_orch": managed_by_orch,
        "capabilities": capabilities
        or {
            "directory_routing": True,
            "event_sse": True,
            "abort": True,
            "instance_dispose": True,
            "attach_fork": True,
        },
    }


def identity_matches(
    reg: dict[str, Any],
    *,
    pid: int | None = None,
    server_nonce: str | None = None,
    server_id: str | None = None,
) -> bool:
    if server_id is not None and reg.get("server_id") != server_id:
        return False
    if server_nonce is not None and reg.get("server_nonce") != server_nonce:
        return False
    if pid is not None and int(reg.get("pid") or 0) != int(pid):
        return False
    return True


def registry_owner_alive(reg: dict[str, Any]) -> bool | None:
    pid = int(reg.get("pid") or 0)
    host = str(reg.get("hostname") or "")
    return _pid_alive(pid, host)


def port_listener_pid(host: str, port: int) -> int | None:
    """Best-effort: find PID listening on host:port (Windows + POSIX)."""
    if os.name == "nt":
        try:
            completed = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            needle = f"{host}:{port}"
            for line in (completed.stdout or "").splitlines():
                if "LISTENING" not in line:
                    continue
                if needle in line or f"0.0.0.0:{port}" in line or f"[::]:{port}" in line:
                    parts = line.split()
                    if parts:
                        try:
                            return int(parts[-1])
                        except ValueError:
                            continue
        except (OSError, subprocess.TimeoutExpired):
            return None
        return None

    try:
        completed = subprocess.run(
            ["ss", "-ltnp"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        for line in (completed.stdout or "").splitlines():
            if f":{port}" in line and "pid=" in line:
                idx = line.find("pid=")
                if idx >= 0:
                    num = ""
                    for ch in line[idx + 4 :]:
                        if ch.isdigit():
                            num += ch
                        else:
                            break
                    if num:
                        return int(num)
    except (OSError, FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def count_active_agent_runs() -> int:
    """Scan all project DBs for non-terminal agent_runs (blocks runtime stop)."""
    from orch.constants import orchestrator_home
    from orch.db import connect
    from orch.migrations import user_version

    data_root = orchestrator_home() / "data"
    if not data_root.is_dir():
        return 0
    total = 0
    for proj_dir in data_root.iterdir():
        if not proj_dir.is_dir():
            continue
        db_path = proj_dir / "orchestrator.db"
        if not db_path.exists():
            continue
        try:
            conn = connect(db_path)
        except Exception:  # noqa: BLE001
            continue
        try:
            if user_version(conn) < 2:
                continue
            row = conn.execute(
                """
                SELECT COUNT(*) AS c FROM agent_runs
                WHERE state NOT IN ('exited', 'archived')
                """
            ).fetchone()
            total += int(row[0] if row else 0)
        except Exception:  # noqa: BLE001
            continue
        finally:
            conn.close()
    return total


def redact_secrets(text: str, *secrets_values: str | None) -> str:
    out = text
    for s in secrets_values:
        if s:
            out = out.replace(s, "***")
    return out


# Re-export path helpers for commands
__all__ = [
    "RuntimeRegistryError",
    "build_registry_record",
    "count_active_agent_runs",
    "ensure_runtime_dirs",
    "identity_matches",
    "load_credentials",
    "load_registry",
    "new_server_id",
    "new_server_nonce",
    "port_listener_pid",
    "public_registry_view",
    "redact_secrets",
    "registry_owner_alive",
    "runtime_credentials_path",
    "runtime_lock_path",
    "runtime_log_path",
    "runtime_registry_path",
    "save_credentials",
    "save_registry",
    "DEFAULT_RUNTIME_HOST",
    "DEFAULT_RUNTIME_PORT",
    "DEFAULT_RUNTIME_USERNAME",
]
