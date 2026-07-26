"""Runtime start / status / stop orchestration (V12-004)."""

from __future__ import annotations

import os
import secrets
import signal
import time
from typing import Any
from urllib.parse import urlparse

from orch.constants import (
    DEFAULT_RUNTIME_HOST,
    DEFAULT_RUNTIME_PORT,
    DEFAULT_RUNTIME_USERNAME,
)
from orch.errors import ValidationError
from orch.locks import _pid_alive, acquire, release
from orch.runtime.http_client import HttpError, OpenCodeHttpClient
from orch.runtime.process import (
    RuntimeProcessError,
    start_opencode_serve,
    wait_for_health,
)
from orch.runtime.registry import (
    RuntimeRegistryError,
    build_registry_record,
    count_active_agent_runs,
    ensure_runtime_dirs,
    identity_matches,
    load_credentials,
    load_registry,
    new_server_id,
    new_server_nonce,
    port_listener_pid,
    public_registry_view,
    registry_owner_alive,
    runtime_credentials_path,
    runtime_lock_path,
    runtime_log_path,
    save_credentials,
    save_registry,
)
from orch.util import utc_now_iso

# Keep log handles alive so Windows child stderr stays valid.
_OPEN_LOG_HANDLES: list[Any] = []


def _parse_base_url(base_url: str) -> tuple[str, int]:
    parsed = urlparse(base_url)
    host = parsed.hostname or DEFAULT_RUNTIME_HOST
    port = parsed.port or DEFAULT_RUNTIME_PORT
    return host, int(port)


def _health(
    base_url: str, *, username: str | None, password: str | None
) -> dict[str, Any] | None:
    try:
        client = OpenCodeHttpClient(
            base_url, username=username, password=password, timeout_sec=3.0
        )
        data = client.get_json("/global/health")
        if isinstance(data, dict) and data.get("healthy") is True:
            return data
    except (HttpError, OSError, RuntimeError):
        return None
    return None


def _creds_for(reg: dict[str, Any] | None) -> tuple[str, str | None]:
    username = DEFAULT_RUNTIME_USERNAME
    password = None
    if not reg:
        return username, password
    creds = load_credentials()
    if creds and creds.get("server_id") == reg.get("server_id"):
        username = str(creds.get("username") or username)
        password = creds.get("password")
    return username, password


def runtime_status() -> dict[str, Any]:
    reg = load_registry()
    username, password = _creds_for(reg)
    view = public_registry_view(reg) or {}
    alive = registry_owner_alive(reg) if reg else None
    healthy = None
    if reg and reg.get("base_url"):
        h = _health(str(reg["base_url"]), username=username, password=password)
        healthy = h is not None
        if h:
            view["server_version"] = h.get("version")
    return {
        "registry": view if view else None,
        "credentials_present": load_credentials() is not None,
        "owner_alive": alive,
        "healthy": healthy,
        "active_agent_runs": count_active_agent_runs(),
        "lock_path": str(runtime_lock_path()),
    }


def runtime_start(
    *,
    port: int | None = None,
    host: str = DEFAULT_RUNTIME_HOST,
    password: str | None = None,
    username: str | None = None,
    external_base_url: str | None = None,
) -> dict[str, Any]:
    """
    Start or reconnect managed OpenCode Server.

    Healthy matching registry -> reuse.
    Dead managed owner -> restart.
    Port owned by unknown process -> refuse (never kill unknown).
    """
    ensure_runtime_dirs()
    username = username or DEFAULT_RUNTIME_USERNAME
    port = DEFAULT_RUNTIME_PORT if port is None else int(port)
    handle = acquire(runtime_lock_path(), command="runtime.start", project=None)
    try:
        if external_base_url:
            return _register_external(
                external_base_url.rstrip("/"),
                username=username,
                password=password,
            )

        existing = load_registry()
        if existing and existing.get("base_url"):
            reused = _try_reuse(existing, username=username, password=password)
            if reused is not None:
                return reused

        listener = port_listener_pid(host, port)
        if listener is not None:
            if not (existing and identity_matches(existing, pid=listener)):
                raise RuntimeRegistryError(
                    "port occupied by unknown process; refusing to start or kill",
                    kind="runtime_port_conflict",
                    details={
                        "host": host,
                        "port": port,
                        "listener_pid": listener,
                        "registry_pid": (existing or {}).get("pid"),
                    },
                )

        return _start_managed(
            host=host,
            port=port,
            username=username,
            password=password,
            previous=existing,
        )
    finally:
        release(handle)


def _register_external(
    base_url: str, *, username: str, password: str | None
) -> dict[str, Any]:
    h = _health(base_url, username=username, password=password)
    if h is None:
        raise RuntimeRegistryError(
            "external Server is not healthy",
            kind="runtime_external_unhealthy",
            details={"base_url": base_url},
        )
    server_id = new_server_id()
    nonce = new_server_nonce()
    listener = port_listener_pid(*_parse_base_url(base_url))
    rec = build_registry_record(
        server_id=server_id,
        base_url=base_url,
        pid=int(listener or 0),
        server_generation=1,
        server_nonce=nonce,
        managed_by_orch=False,
    )
    if password is not None:
        save_credentials(username=username, password=password, server_id=server_id)
    else:
        # Passwordless external Server: still write credentials file so workers
        # can resolve username without treating registry as incomplete.
        save_credentials(username=username, password="", server_id=server_id)
    save_registry(rec)
    return {
        "action": "registered_external",
        "registry": public_registry_view(rec),
        "healthy": True,
        "server_version": h.get("version"),
    }


def _try_reuse(
    existing: dict[str, Any],
    *,
    username: str,
    password: str | None,
) -> dict[str, Any] | None:
    use_user, use_pass = username, password
    if use_pass is None:
        cu, cp = _creds_for(existing)
        use_user, use_pass = cu, cp
    h = _health(str(existing["base_url"]), username=use_user, password=use_pass)
    if h is None:
        return None
    owner_alive = registry_owner_alive(existing)
    # Healthy Server: reuse whether managed or external. Do not restart.
    return {
        "action": "reused",
        "registry": public_registry_view(existing),
        "healthy": True,
        "server_version": h.get("version"),
        "owner_alive": owner_alive,
    }


def _start_managed(
    *,
    host: str,
    port: int,
    username: str,
    password: str | None,
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    gen = 1
    if previous and previous.get("managed_by_orch"):
        gen = int(previous.get("server_generation") or 0) + 1
    server_id = new_server_id()
    nonce = new_server_nonce()
    password = password or secrets.token_urlsafe(18)

    log_path = runtime_log_path()
    log_fp = open(log_path, "a", encoding="utf-8")  # noqa: SIM115
    _OPEN_LOG_HANDLES.append(log_fp)
    try:
        managed = start_opencode_serve(
            host=host,
            port=port,
            password=password,
            username=username,
            pure=True,
            log_file=log_fp,
        )
    except RuntimeProcessError as exc:
        raise RuntimeRegistryError(str(exc), kind="runtime_start_failed") from exc

    base_url = managed.base_url
    try:
        health = wait_for_health(
            base_url, password=password, username=username, timeout_sec=45.0
        )
    except Exception as exc:  # noqa: BLE001
        managed.terminate()
        raise RuntimeRegistryError(
            f"Server failed health check: {exc}",
            kind="runtime_unhealthy",
        ) from exc

    rec = build_registry_record(
        server_id=server_id,
        base_url=base_url,
        pid=int(managed.process.pid or 0),
        server_generation=gen,
        server_nonce=nonce,
        managed_by_orch=True,
    )
    cred_path = save_credentials(
        username=username, password=password, server_id=server_id
    )
    save_registry(rec)
    return {
        "action": "started",
        "registry": public_registry_view(rec),
        "healthy": True,
        "server_version": health.get("version"),
        "credentials_path": str(cred_path),
    }


def runtime_stop(*, force: bool = False) -> dict[str, Any]:
    """Stop orch-managed Server only. Never kill external/unknown owners."""
    ensure_runtime_dirs()
    handle = acquire(runtime_lock_path(), command="runtime.stop", project=None)
    try:
        active = count_active_agent_runs()
        if active > 0 and not force:
            raise ValidationError(
                f"refusing runtime stop: {active} active agent run(s)",
                kind="runtime_stop_blocked",
                details={"active_agent_runs": active},
            )

        reg = load_registry()
        if reg is None:
            return {"action": "noop", "reason": "no registry"}

        if not reg.get("managed_by_orch"):
            raise RuntimeRegistryError(
                "refusing to stop external/unmanaged Server",
                kind="runtime_stop_external",
                details={"server_id": reg.get("server_id")},
            )

        pid = int(reg.get("pid") or 0)
        host = str(reg.get("hostname") or "")
        if registry_owner_alive(reg) is True and pid > 0:
            base_url = str(reg.get("base_url") or "")
            try:
                host_u, port_u = _parse_base_url(base_url)
            except Exception:  # noqa: BLE001
                host_u, port_u = DEFAULT_RUNTIME_HOST, DEFAULT_RUNTIME_PORT
            listener = port_listener_pid(host_u, port_u)
            if listener is not None and listener != pid:
                raise RuntimeRegistryError(
                    "registry PID does not match port listener; refuse kill",
                    kind="runtime_identity_mismatch",
                    details={"registry_pid": pid, "listener_pid": listener},
                )
            _terminate_pid(pid, host)

        updated = dict(reg)
        updated["pid"] = 0
        updated["stopped_at"] = utc_now_iso()
        updated["state"] = "stopped"
        save_registry(updated)
        return {
            "action": "stopped",
            "registry": public_registry_view(load_registry()),
            "forced": force,
            "credentials_path": str(runtime_credentials_path()),
        }
    finally:
        release(handle)


def _terminate_pid(pid: int, hostname: str) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        raise RuntimeRegistryError(
            f"failed to signal managed Server: {exc}",
            kind="runtime_stop_failed",
        ) from exc
    for _ in range(20):
        if _pid_alive(pid, hostname) is False:
            return
        time.sleep(0.25)
    kill_sig = getattr(signal, "SIGKILL", signal.SIGTERM)
    try:
        os.kill(pid, kill_sig)
    except OSError:
        pass
