"""Agent lifecycle CLI commands (V12-008 / V12-010)."""

from __future__ import annotations

from typing import Any

from orch.runtime.lifecycle import AgentLifecycleService
from orch.runtime.takeover import (
    agent_open,
    direct_takeover,
    fork_inspect,
    release_control,
)


def cmd_agent_start(
    project: str,
    agent: str,
    branch: str,
    worktree_path: str,
    *,
    prompt: str | None = None,
    prompt_file: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    text = prompt
    if prompt_file:
        from pathlib import Path

        text = Path(prompt_file).read_text(encoding="utf-8")
    svc = AgentLifecycleService(project)
    return svc.start(
        agent=agent,
        branch=branch,
        worktree_path=worktree_path,
        prompt=text,
        session_id=session_id,
    )


def cmd_agent_stop(
    project: str,
    run_id: str,
    *,
    force_after: float = 5.0,
) -> dict[str, Any]:
    return AgentLifecycleService(project).stop(run_id, force_after_sec=force_after)


def cmd_agent_reconcile(
    project: str, run_id: str | None = None
) -> dict[str, Any]:
    return AgentLifecycleService(project).reconcile(run_id)


def cmd_agent_archive(project: str, run_id: str) -> dict[str, Any]:
    return AgentLifecycleService(project).archive(run_id)


def cmd_agent_takeover(
    project: str,
    run_id: str,
    *,
    fork: bool = False,
    launch: bool = False,
) -> dict[str, Any]:
    if fork:
        return fork_inspect(project, run_id)
    return direct_takeover(project, run_id, launch=launch)


def cmd_agent_release(
    project: str,
    run_id: str,
    *,
    token: str,
    resume: bool = False,
    launch: bool = False,
) -> dict[str, Any]:
    return release_control(
        project, run_id, lease_token=token, resume=resume, launch=launch
    )


def cmd_agent_open(
    project: str,
    run_id: str,
    *,
    fork: bool = False,
    launch: bool = False,
) -> dict[str, Any]:
    return agent_open(project, run_id, fork=fork, launch=launch)
