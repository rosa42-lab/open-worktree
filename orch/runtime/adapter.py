"""RuntimeAdapter protocol boundary (V12-005)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class CapabilityMatrix:
    global_health: bool
    directory_header: bool
    directory_query: bool
    create_session: bool
    get_session: bool
    session_status: bool
    event_sse: bool
    abort: bool
    instance_dispose: bool
    prompt_async: bool
    session_fork_api: bool
    attach_cli_dir: bool
    attach_cli_session: bool
    attach_cli_fork: bool
    basic_auth: bool
    path_api: bool
    vcs_api: bool
    shell_api: bool

    def as_dict(self) -> dict[str, bool]:
        return {
            "global_health": self.global_health,
            "directory_header": self.directory_header,
            "directory_query": self.directory_query,
            "create_session": self.create_session,
            "get_session": self.get_session,
            "session_status": self.session_status,
            "event_sse": self.event_sse,
            "abort": self.abort,
            "instance_dispose": self.instance_dispose,
            "prompt_async": self.prompt_async,
            "session_fork_api": self.session_fork_api,
            "attach_cli_dir": self.attach_cli_dir,
            "attach_cli_session": self.attach_cli_session,
            "attach_cli_fork": self.attach_cli_fork,
            "basic_auth": self.basic_auth,
            "path_api": self.path_api,
            "vcs_api": self.vcs_api,
            "shell_api": self.shell_api,
        }

    @property
    def required_pass(self) -> bool:
        """Phase 0 hard requirements for shared-server architecture."""
        return all(
            [
                self.global_health,
                self.directory_header,
                self.create_session,
                self.event_sse,
                self.abort,
                self.instance_dispose,
                self.attach_cli_dir,
                self.attach_cli_session,
                self.attach_cli_fork,
            ]
        )


@runtime_checkable
class RuntimeAdapter(Protocol):
    def health(self) -> dict[str, Any]: ...

    def capabilities(self) -> CapabilityMatrix: ...

    def create_session(
        self, directory: str, *, title: str | None = None
    ) -> dict[str, Any]: ...

    def get_session(self, directory: str, session_id: str) -> dict[str, Any]: ...

    def get_status(self, directory: str, session_id: str | None = None) -> dict[str, Any]: ...

    def send_prompt_async(
        self, directory: str, session_id: str, *, text: str
    ) -> None: ...

    def subscribe_events(
        self,
        directory: str,
        *,
        cursor: str | None = None,
        timeout_sec: float = 8.0,
        idle_sec: float = 2.0,
        max_events: int = 30,
    ) -> list[dict[str, Any]]: ...

    def abort(self, directory: str, session_id: str) -> Any: ...

    def dispose_instance(self, directory: str) -> Any: ...

    def build_attach_command(
        self,
        directory: str,
        session_id: str,
        *,
        fork: bool = False,
        base_url: str | None = None,
    ) -> str: ...


# Back-compat: historical import path `orch.runtime.adapter.OpenCodeRuntimeAdapter`
from orch.runtime.opencode import OpenCodeRuntimeAdapter  # noqa: E402

__all__ = [
    "CapabilityMatrix",
    "RuntimeAdapter",
    "OpenCodeRuntimeAdapter",
]
