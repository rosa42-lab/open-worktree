"""Managed OpenCode Server subprocess helpers (probe / runtime).

Separate from orch.git — OpenCode/worker processes must not share the Git runner.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from typing import IO


class RuntimeProcessError(Exception):
    pass


def find_opencode_bin() -> str:
    path = shutil.which("opencode")
    if not path:
        raise RuntimeProcessError(
            "opencode executable not found on PATH; install OpenCode >= 1.18.5"
        )
    return path


def pick_free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


@dataclass
class ManagedServer:
    host: str
    port: int
    process: subprocess.Popen[str]
    log_path: str | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def is_alive(self) -> bool:
        return self.process.poll() is None

    def terminate(self, *, grace_sec: float = 5.0) -> None:
        if self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=grace_sec)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5.0)


def start_opencode_serve(
    *,
    host: str = "127.0.0.1",
    port: int | None = None,
    password: str | None = None,
    username: str | None = None,
    pure: bool = True,
    log_file: IO[str] | None = None,
) -> ManagedServer:
    """Start `opencode serve` on loopback. Does not touch orch project DB/locks."""
    bin_path = find_opencode_bin()
    if port is None:
        port = pick_free_port(host)
    cmd = [
        bin_path,
        "serve",
        "--hostname",
        host,
        "--port",
        str(port),
    ]
    if pure:
        cmd.append("--pure")
    env = os.environ.copy()
    if password is not None:
        env["OPENCODE_SERVER_PASSWORD"] = password
    if username is not None:
        env["OPENCODE_SERVER_USERNAME"] = username
    # Prefer not inheriting unrelated secrets into child argv; password is env-only.
    proc = subprocess.Popen(
        cmd,
        stdout=log_file or subprocess.DEVNULL,
        stderr=log_file or subprocess.DEVNULL,
        env=env,
        text=True,
    )
    return ManagedServer(host=host, port=port, process=proc)


def wait_for_health(
    base_url: str,
    *,
    password: str | None = None,
    username: str | None = None,
    timeout_sec: float = 30.0,
) -> dict:
    """Poll /global/health until healthy or raise."""
    from orch.runtime.http_client import HttpError, OpenCodeHttpClient

    client = OpenCodeHttpClient(base_url, username=username, password=password, timeout_sec=5.0)
    deadline = time.monotonic() + timeout_sec
    last_err: str | None = None
    while time.monotonic() < deadline:
        try:
            data = client.get_json("/global/health")
            if isinstance(data, dict) and data.get("healthy") is True:
                return data
            last_err = f"unexpected health payload: {data!r}"
        except HttpError as exc:
            last_err = str(exc)
        time.sleep(0.25)
    raise RuntimeProcessError(f"OpenCode server health timeout: {last_err}")


def opencode_cli_version() -> str:
    bin_path = find_opencode_bin()
    try:
        out = subprocess.run(
            [bin_path, "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeProcessError(f"failed to read opencode --version: {exc}") from exc
    return (out.stdout or out.stderr or "").strip().splitlines()[0].strip()


def attach_help_supports_flags() -> dict[str, bool]:
    """Parse `opencode attach --help` for --dir/--session/--fork."""
    bin_path = find_opencode_bin()
    try:
        out = subprocess.run(
            [bin_path, "attach", "--help"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeProcessError(f"failed to run opencode attach --help: {exc}") from exc
    text = f"{out.stdout}\n{out.stderr}".lower()
    return {
        "dir": "--dir" in text,
        "session": "--session" in text or "-s," in text or "-s " in text,
        "fork": "--fork" in text,
        "password": "--password" in text or "-p," in text,
        "username": "--username" in text or "-u," in text,
    }
