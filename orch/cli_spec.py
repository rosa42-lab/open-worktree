"""Declarative project command registry (V17).

PROJECT_COMMANDS, parser names, help text, and lock_for(args) all come from
PROJECT_SPECS. lock_for documents project.lock for this invocation; it does
not acquire the lock.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Literal

LockKind = Literal["project", "none", "flag"]


@dataclass(frozen=True)
class CommandSpec:
    name: str
    help: str
    lock: LockKind


PROJECT_SPECS: tuple[CommandSpec, ...] = (
    CommandSpec("init", "initialize main worktree and DB", "project"),
    CommandSpec("worktree-add", "create agent worktree", "project"),
    CommandSpec("enqueue", "enqueue branch for merge", "project"),
    CommandSpec("list", "list tasks", "none"),
    CommandSpec("pending", "list pending tasks", "none"),
    CommandSpec("diff", "show frozen diff", "none"),
    CommandSpec("changes", "show changes summary", "none"),
    CommandSpec("log", "show commit log", "none"),
    CommandSpec("merge", "merge next pending task(s)", "project"),
    CommandSpec("retry", "retry a conflict task", "project"),
    CommandSpec("skip", "skip a pending/conflict task", "project"),
    CommandSpec("reset-stuck", "evidence-based recovery for stuck tasks", "project"),
    CommandSpec("cleanup", "list or prune merged worktrees", "flag"),
    CommandSpec("lock-status", "show project lock status", "none"),
    CommandSpec("lock-break", "break stale project lock", "none"),
    CommandSpec("agent-list", "list agent runs (observe-only)", "none"),
    CommandSpec("agent-show", "show one agent run (observe-only)", "none"),
    CommandSpec("agent-watch", "watch one agent run (observe-only)", "none"),
    CommandSpec(
        "agent-register",
        "temporarily register existing worktree/session for observe (no worker)",
        "none",
    ),
    CommandSpec("agent-start", "start agent worker against registered runtime", "project"),
    CommandSpec("agent-stop", "stop agent worker", "project"),
    CommandSpec("agent-reconcile", "reconcile agent run(s) from evidence", "project"),
    CommandSpec("agent-archive", "archive a terminal agent run", "project"),
    CommandSpec("agent-takeover", "direct takeover or --fork inspect", "project"),
    CommandSpec("agent-release", "release human control; optional --resume", "project"),
    CommandSpec("agent-open", "print attach locator; --launch starts client", "flag"),
    CommandSpec("coordinator-bind", "bind root coordinator session", "project"),
    CommandSpec("coordinator-show", "show active coordinator binding", "none"),
    CommandSpec(
        "topic-start",
        "provision isolated topic branch+worktree under coordinator",
        "project",
    ),
    CommandSpec("topic-list", "list topics", "none"),
    CommandSpec("topic-show", "show topic + coordinator + run", "none"),
    CommandSpec("topic-open", "open topic session locator", "flag"),
    CommandSpec("topic-ready", "mark topic ready_for_enqueue (no enqueue)", "project"),
    CommandSpec("topic-enqueue", "enqueue a ready topic (does not merge)", "project"),
    CommandSpec("topic-abandon", "cancel a proposed/active/ready topic", "project"),
    CommandSpec("topic-archive", "archive topic product record", "project"),
    CommandSpec(
        "doctor",
        "read-only diagnose of schema, topic graph, and runtime stale hints",
        "none",
    ),
    CommandSpec(
        "remote-config",
        "write non-secret remote/provider promotion config",
        "none",
    ),
    CommandSpec("remote-probe", "read-only probe of git/provider capabilities", "none"),
    CommandSpec(
        "remote-status",
        "show local/remote develop/master SHA relations",
        "none",
    ),
    CommandSpec(
        "promote-develop",
        "dry-run or CAS push local develop to origin/develop",
        "project",
    ),
    CommandSpec("promotion-list", "list promotion_runs for project", "none"),
    CommandSpec("promotion-show", "show promotion run + events + tasks", "none"),
    CommandSpec("promotion-reconcile", "read remote tip; never blind-push", "project"),
    CommandSpec(
        "promotion-cancel",
        "cancel a non-terminal promotion with reason",
        "project",
    ),
    CommandSpec(
        "release-create",
        "create develop→master Promotion PR (default dry-run)",
        "project",
    ),
    CommandSpec("release-status", "observe Promotion PR; never mark released", "project"),
    CommandSpec(
        "release-sync",
        "FF-sync release merge commit back to develop",
        "project",
    ),
)

PROJECT_COMMANDS = frozenset(spec.name for spec in PROJECT_SPECS)
_SPEC_BY_NAME = {spec.name: spec for spec in PROJECT_SPECS}


def lock_for(args: argparse.Namespace) -> str | None:
    """Return 'project' when this invocation takes project.lock, else None."""
    cmd = getattr(args, "cmd", None)
    spec = _SPEC_BY_NAME.get(str(cmd)) if cmd else None
    if spec is None:
        return None
    if spec.lock == "project":
        return "project"
    if spec.lock == "none":
        return None
    if cmd == "cleanup" and bool(getattr(args, "prune", False)):
        return "project"
    if cmd in ("topic-open", "agent-open") and bool(getattr(args, "fork", False)):
        return "project"
    return None
