"""OpenCode RuntimeAdapter implementation (V12-005)."""

from __future__ import annotations

from typing import Any

from orch.runtime.adapter import CapabilityMatrix
from orch.runtime.http_client import OpenCodeHttpClient


class OpenCodeRuntimeAdapter:
    """
    Encapsulates OpenCode HTTP/SSE details.

    Lifecycle/command handlers must not call OpenCodeHttpClient directly.
    """

    def __init__(
        self,
        client: OpenCodeHttpClient,
        *,
        known_capabilities: CapabilityMatrix | None = None,
    ) -> None:
        self.client = client
        self._known_capabilities = known_capabilities

    def health(self) -> dict[str, Any]:
        data = self.client.get_json("/global/health")
        if not isinstance(data, dict):
            raise RuntimeError(f"unexpected health response: {data!r}")
        return data

    def capabilities(self) -> CapabilityMatrix:
        if self._known_capabilities is not None:
            return self._known_capabilities
        # Minimal live probe without mutating sessions.
        healthy = False
        try:
            h = self.health()
            healthy = bool(h.get("healthy"))
        except Exception:  # noqa: BLE001
            healthy = False
        return CapabilityMatrix(
            global_health=healthy,
            directory_header=True,
            directory_query=False,
            create_session=True,
            get_session=True,
            session_status=True,
            event_sse=True,
            abort=True,
            instance_dispose=True,
            prompt_async=True,
            session_fork_api=True,
            attach_cli_dir=True,
            attach_cli_session=True,
            attach_cli_fork=True,
            basic_auth=bool(self.client.password),
            path_api=False,
            vcs_api=False,
            shell_api=False,
        )

    def create_session(
        self, directory: str, *, title: str | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if title is not None:
            body["title"] = title
        data = self.client.post_json("/session", directory=directory, json_body=body)
        if not isinstance(data, dict) or "id" not in data:
            raise RuntimeError(f"unexpected create_session response: {data!r}")
        return data

    def get_session(self, directory: str, session_id: str) -> dict[str, Any]:
        data = self.client.get_json(f"/session/{session_id}", directory=directory)
        if not isinstance(data, dict):
            raise RuntimeError(f"unexpected get_session response: {data!r}")
        return data

    def list_sessions(self, directory: str) -> list[dict[str, Any]]:
        data = self.client.get_json("/session", directory=directory)
        if not isinstance(data, list):
            raise RuntimeError(f"unexpected list_sessions response: {data!r}")
        return data

    def get_status(
        self, directory: str, session_id: str | None = None
    ) -> dict[str, Any]:
        # OpenCode exposes aggregate /session/status; session_id kept for Protocol.
        _ = session_id
        data = self.client.get_json("/session/status", directory=directory)
        if not isinstance(data, dict):
            raise RuntimeError(f"unexpected status response: {data!r}")
        return data

    def get_path(self, directory: str) -> dict[str, Any]:
        data = self.client.get_json("/path", directory=directory)
        if not isinstance(data, dict):
            raise RuntimeError(f"unexpected path response: {data!r}")
        return data

    def get_vcs(self, directory: str) -> dict[str, Any]:
        data = self.client.get_json("/vcs", directory=directory)
        if not isinstance(data, dict):
            raise RuntimeError(f"unexpected vcs response: {data!r}")
        return data

    def get_project_current(self, directory: str) -> dict[str, Any]:
        data = self.client.get_json("/project/current", directory=directory)
        if not isinstance(data, dict):
            raise RuntimeError(f"unexpected project/current response: {data!r}")
        return data

    def abort(self, directory: str, session_id: str) -> Any:
        return self.client.post_json(
            f"/session/{session_id}/abort",
            directory=directory,
            json_body={},
        )

    def dispose_instance(self, directory: str) -> Any:
        return self.client.post_json(
            "/instance/dispose",
            directory=directory,
            json_body={},
        )

    def fork_session(
        self, directory: str, session_id: str, *, message_id: str | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if message_id is not None:
            body["messageID"] = message_id
        data = self.client.post_json(
            f"/session/{session_id}/fork",
            directory=directory,
            json_body=body,
        )
        if not isinstance(data, dict) or "id" not in data:
            raise RuntimeError(f"unexpected fork response: {data!r}")
        return data

    def send_prompt_async(
        self,
        directory: str,
        session_id: str,
        *,
        text: str,
    ) -> None:
        self.prompt_async(directory, session_id, text=text)

    def prompt_async(
        self,
        directory: str,
        session_id: str,
        *,
        text: str,
    ) -> None:
        body = {
            "parts": [{"type": "text", "text": text}],
        }
        self.client.post_json(
            f"/session/{session_id}/prompt_async",
            directory=directory,
            json_body=body,
            expect_empty=True,
        )

    def shell(
        self,
        directory: str,
        session_id: str,
        *,
        command: str,
        agent: str = "build",
    ) -> Any:
        return self.client.post_json(
            f"/session/{session_id}/shell",
            directory=directory,
            json_body={"agent": agent, "command": command},
            timeout_sec=60.0,
        )

    def subscribe_events(
        self,
        directory: str,
        *,
        cursor: str | None = None,
        timeout_sec: float = 8.0,
        idle_sec: float = 2.0,
        max_events: int = 30,
    ) -> list[dict[str, Any]]:
        query = {"cursor": cursor} if cursor else None
        return self.client.iter_sse(
            "/event",
            directory=directory,
            query=query,
            timeout_sec=timeout_sec,
            idle_sec=idle_sec,
            max_events=max_events,
        )

    def build_attach_command(
        self,
        directory: str,
        session_id: str,
        *,
        fork: bool = False,
        base_url: str | None = None,
    ) -> str:
        url = base_url or self.client.base_url
        parts = [
            "opencode",
            "attach",
            url,
            "--dir",
            directory,
            "--session",
            session_id,
        ]
        if fork:
            parts.append("--fork")
        return " ".join(_win_quote(p) for p in parts)


def _win_quote(value: str) -> str:
    if not value:
        return '""'
    if any(ch in value for ch in (' ', '"', "\t")):
        return '"' + value.replace('"', '\\"') + '"'
    return value
