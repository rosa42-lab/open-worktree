"""argparse CLI entry (task T-0001 / T-0102)."""

from __future__ import annotations

import argparse
import sys
import traceback
from typing import Any, Callable, Sequence

from orch import __version__
from orch.errors import ExitCode, OrchError, UsageError
from orch.jsonio import emit_json, envelope_from_exception, success_envelope


def _add_json(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON envelope",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="orch",
        description="Multi-agent worktree orchestrator (v1.1 / v1.2 runtime probe)",
    )
    parser.add_argument("--version", action="version", version=f"orch {__version__}")
    _add_json(parser)

    # We use a two-phase parse: first token may be "project", "runtime", or <project-name>.
    # argparse with subparsers is awkward for `orch <project> <cmd>`, so we parse manually
    # after a lightweight structure.
    return parser


PROJECT_COMMANDS = frozenset(
    {
        "init",
        "worktree-add",
        "enqueue",
        "list",
        "pending",
        "diff",
        "changes",
        "log",
        "merge",
        "retry",
        "skip",
        "reset-stuck",
        "cleanup",
        "lock-status",
        "lock-break",
        "agent-list",
        "agent-show",
        "agent-watch",
        "agent-register",
        "agent-start",
        "agent-stop",
        "agent-reconcile",
        "agent-archive",
        "agent-takeover",
        "agent-release",
        "agent-open",
        "coordinator-bind",
        "coordinator-show",
        "topic-start",
        "topic-list",
        "topic-show",
        "topic-open",
        "topic-ready",
        "topic-archive",
        "remote-config",
        "remote-probe",
        "remote-status",
        "promote-develop",
        "promotion-list",
        "promotion-show",
        "promotion-reconcile",
        "promotion-cancel",
        "release-create",
        "release-status",
        "release-sync",
    }
)

RUNTIME_COMMANDS = frozenset({"probe", "start", "status", "stop"})


def _parser_project_group() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="orch project")
    _add_json(p)
    sub = p.add_subparsers(dest="project_cmd", required=True)
    pl = sub.add_parser("list", help="list registered projects")
    _add_json(pl)
    pa = sub.add_parser("add", help="register a project")
    pa.add_argument("name")
    pa.add_argument("path")
    _add_json(pa)
    pr = sub.add_parser("remove", help="unregister a project (does not delete data)")
    pr.add_argument("name")
    _add_json(pr)
    return p


def _parser_runtime_group() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="orch runtime")
    _add_json(p)
    sub = p.add_subparsers(dest="runtime_cmd", required=True)
    probe = sub.add_parser(
        "probe",
        help="probe OpenCode Server capabilities (no project DB/lock)",
    )
    _add_json(probe)
    probe.add_argument(
        "--base-url",
        default=None,
        help="reuse an existing Server (default: start ephemeral managed serve)",
    )
    probe.add_argument(
        "--port",
        type=int,
        default=None,
        help="port for managed ephemeral Server (default: free port)",
    )
    probe.add_argument(
        "--password",
        default=None,
        help="OPENCODE_SERVER_PASSWORD (or for managed serve: generate ephemeral)",
    )
    probe.add_argument(
        "--username",
        default=None,
        help="OPENCODE_SERVER_USERNAME (default: opencode)",
    )
    probe.add_argument(
        "--keep-server",
        action="store_true",
        help="do not terminate managed Server after probe (for Desktop H2)",
    )
    for name, help_ in (
        ("start", "start or reuse managed OpenCode Server"),
        ("status", "show runtime Server registry status"),
        ("stop", "stop orch-managed OpenCode Server"),
    ):
        sp = sub.add_parser(name, help=help_)
        _add_json(sp)
        if name == "start":
            sp.add_argument("--port", type=int, default=None)
            sp.add_argument("--password", default=None)
            sp.add_argument("--username", default=None)
            sp.add_argument(
                "--base-url",
                default=None,
                help="register an external healthy Server instead of starting",
            )
        if name == "stop":
            sp.add_argument(
                "--force",
                action="store_true",
                help="stop even if active agent runs exist (dangerous)",
            )
    return p


def _parser_for_project(project: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=f"orch {project}")
    _add_json(p)
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name: str, help_: str) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, help=help_)
        _add_json(sp)
        return sp

    add("init", "initialize main worktree and DB")
    w = add("worktree-add", "create agent worktree")
    w.add_argument("agent")
    w.add_argument("branch")
    w.add_argument("--base", default="develop")

    e = add("enqueue", "enqueue branch for merge")
    e.add_argument("agent")
    e.add_argument("branch")
    e.add_argument("worktree_path")
    e.add_argument("--priority", type=int, default=1)

    ls = add("list", "list tasks")
    ls.add_argument("--all", action="store_true")

    add("pending", "list pending tasks")
    d = add("diff", "show frozen diff")
    d.add_argument("task")
    c = add("changes", "show changes summary")
    c.add_argument("task")
    lg = add("log", "show commit log")
    lg.add_argument("task")

    m = add("merge", "merge next pending task(s)")
    m.add_argument("--once", action="store_true")

    r = add("retry", "retry a conflict task")
    r.add_argument("task_id")
    sk = add("skip", "skip a pending/conflict task")
    sk.add_argument("task_id")
    sk.add_argument("--reason", default="")
    add("reset-stuck", "evidence-based recovery for stuck tasks")
    cl = add("cleanup", "list or prune merged worktrees")
    cl.add_argument("--prune", action="store_true")
    add("lock-status", "show project lock status")
    lb = add("lock-break", "break stale project lock")
    lb.add_argument("--force", action="store_true")

    al = add("agent-list", "list agent runs (observe-only)")
    al.add_argument("--all", action="store_true", help="include archived runs")
    al.add_argument("--base-url", default=None, help="optional Server URL for attach locator")

    ash = add("agent-show", "show one agent run (observe-only)")
    ash.add_argument("run_id")
    ash.add_argument("--base-url", default=None)

    aw = add("agent-watch", "watch one agent run (observe-only)")
    aw.add_argument("run_id")
    aw.add_argument("--base-url", default=None)
    aw.add_argument("--interval", type=float, default=1.0)
    aw.add_argument("--ticks", type=int, default=1, help="number of observe ticks")
    aw.add_argument("--username", default=None)
    aw.add_argument("--password", default=None)

    ar = add(
        "agent-register",
        "temporarily register existing worktree/session for observe (no worker)",
    )
    ar.add_argument("agent")
    ar.add_argument("branch")
    ar.add_argument("worktree_path")
    ar.add_argument("--session", required=True)
    ar.add_argument("--server-id", default="external")
    ar.add_argument("--base-url", default=None)

    astart = add("agent-start", "start agent worker against registered runtime")
    astart.add_argument("agent")
    astart.add_argument("branch")
    astart.add_argument("worktree_path")
    astart.add_argument("--prompt", default=None)
    astart.add_argument("--prompt-file", default=None)
    astart.add_argument("--session", default=None, help="reuse existing session id")

    astop = add("agent-stop", "stop agent worker")
    astop.add_argument("run_id")
    astop.add_argument("--force-after", type=float, default=5.0)

    arec = add("agent-reconcile", "reconcile agent run(s) from evidence")
    arec.add_argument("run_id", nargs="?", default=None)

    aarch = add("agent-archive", "archive a terminal agent run")
    aarch.add_argument("run_id")

    atk = add("agent-takeover", "direct takeover or --fork inspect")
    atk.add_argument("run_id")
    atk.add_argument("--fork", action="store_true")
    atk.add_argument("--launch", action="store_true")

    arel = add("agent-release", "release human control; optional --resume")
    arel.add_argument("run_id")
    arel.add_argument("--token", required=True, help="human lease token")
    arel.add_argument("--resume", action="store_true")
    arel.add_argument("--launch", action="store_true")

    aopen = add("agent-open", "print attach locator; --launch starts client")
    aopen.add_argument("run_id")
    aopen.add_argument("--fork", action="store_true")
    aopen.add_argument("--launch", action="store_true")

    cb = add("coordinator-bind", "bind root coordinator session")
    cb.add_argument("--session", required=True)
    cb.add_argument("--directory", required=True)
    cb.add_argument("--server-id", default=None)
    cb.add_argument("--replace", action="store_true")
    add("coordinator-show", "show active coordinator binding")

    ts = add("topic-start", "create a persistent topic under coordinator")
    ts.add_argument("name")
    ts.add_argument("--title", required=True)
    ts.add_argument("--goal", required=True)
    ts.add_argument("--branch", required=True)
    ts.add_argument("--worktree", required=True)

    tl = add("topic-list", "list topics")
    tl.add_argument("--all", action="store_true")
    tsh = add("topic-show", "show topic + coordinator + run")
    tsh.add_argument("topic_id")
    topen = add("topic-open", "open topic session locator")
    topen.add_argument("topic_id")
    topen.add_argument("--fork", action="store_true")
    topen.add_argument("--launch", action="store_true")
    tr = add("topic-ready", "mark topic ready_for_enqueue (no enqueue)")
    tr.add_argument("topic_id")
    tr.add_argument("--commit", required=True, help="verification commit SHA")
    tr.add_argument("--command", action="append", default=[], help="verification command")
    ta = add("topic-archive", "archive topic product record")
    ta.add_argument("topic_id")

    rc = add("remote-config", "write non-secret remote/provider promotion config")
    rc.add_argument("--remote", default="origin")
    rc.add_argument("--provider", default="github")
    rc.add_argument("--repository", required=True, help="owner/name")
    rc.add_argument("--api-base-url", default="https://api.github.com")
    rc.add_argument("--integration", default="develop")
    rc.add_argument("--stable", default="master")
    rc.add_argument(
        "--required-check",
        action="append",
        default=None,
        dest="required_checks",
        help="repeatable required check name",
    )
    rc.add_argument("--required-approvals", type=int, default=1)

    rp = add("remote-probe", "read-only probe of git/provider capabilities")
    rp.add_argument(
        "--no-fetch",
        action="store_true",
        help="skip git fetch (still reports ref slots)",
    )

    rs = add("remote-status", "show local/remote develop/master SHA relations")
    rs.add_argument(
        "--no-fetch",
        action="store_true",
        help="skip git fetch before reading remote-tracking refs",
    )

    pd = add("promote-develop", "dry-run or CAS push local develop to origin/develop")
    pd.add_argument(
        "--execute",
        action="store_true",
        help="perform remote write (default is dry-run plan only)",
    )
    pd.add_argument(
        "--verification",
        default=None,
        help="verification_record id (required with --execute)",
    )
    pd.add_argument(
        "--no-fetch",
        action="store_true",
        help="skip git fetch during precheck",
    )

    pl = add("promotion-list", "list promotion_runs for project")
    pl.add_argument("--kind", default=None, help="develop_publish|master_release")
    pl.add_argument("--limit", type=int, default=50)

    psh = add("promotion-show", "show promotion run + events + tasks")
    psh.add_argument("promotion_id")

    prc = add("promotion-reconcile", "read remote tip; never blind-push")
    prc.add_argument("promotion_id")
    prc.add_argument("--no-fetch", action="store_true")

    pc = add("promotion-cancel", "cancel a non-terminal promotion with reason")
    pc.add_argument("promotion_id")
    pc.add_argument("--reason", required=True)
    pc.add_argument("--actor", default="operator")
    pc.add_argument("--no-fetch", action="store_true")

    rcreate = add("release-create", "create develop→master Promotion PR (default dry-run)")
    rcreate.add_argument("--verification", required=True)
    rcreate.add_argument("--title", default=None)
    rcreate.add_argument("--execute", action="store_true")
    rcreate.add_argument("--no-fetch", action="store_true")

    rstatus = add("release-status", "observe Promotion PR; never mark released")
    rstatus.add_argument("promotion_id")
    rstatus.add_argument("--no-fetch", action="store_true")

    rsync = add("release-sync", "FF-sync release merge commit back to develop")
    rsync.add_argument("promotion_id")
    rsync.add_argument("--execute", action="store_true")
    rsync.add_argument("--no-fetch", action="store_true")
    return p


def _command_name(project: str | None, cmd: str) -> str:
    if project:
        return f"{project}.{cmd}"
    return f"project.{cmd}"


def _dispatch_runtime(args: argparse.Namespace) -> tuple[str, Any]:
    cmd = args.runtime_cmd
    name = f"runtime.{cmd}"
    if cmd == "probe":
        from orch.commands.runtime import cmd_runtime_probe

        return name, cmd_runtime_probe(
            base_url=getattr(args, "base_url", None),
            port=getattr(args, "port", None),
            password=getattr(args, "password", None),
            username=getattr(args, "username", None),
            keep_server=bool(getattr(args, "keep_server", False)),
        )
    if cmd == "start":
        from orch.commands.runtime import cmd_runtime_start

        return name, cmd_runtime_start(
            port=getattr(args, "port", None),
            password=getattr(args, "password", None),
            username=getattr(args, "username", None),
            base_url=getattr(args, "base_url", None),
        )
    if cmd == "status":
        from orch.commands.runtime import cmd_runtime_status

        return name, cmd_runtime_status()
    if cmd == "stop":
        from orch.commands.runtime import cmd_runtime_stop

        return name, cmd_runtime_stop(force=bool(getattr(args, "force", False)))
    raise UsageError(f"unknown runtime command: {cmd}")


def _dispatch(args: argparse.Namespace, *, project: str | None) -> tuple[str, Any]:
    if project is None:
        # project group
        cmd = args.project_cmd
        name = _command_name(None, cmd)
        if cmd == "list":
            from orch.commands.project import cmd_project_list

            return name, cmd_project_list()
        if cmd == "add":
            from orch.commands.project import cmd_project_add

            return name, cmd_project_add(args.name, args.path)
        if cmd == "remove":
            from orch.commands.project import cmd_project_remove

            return name, cmd_project_remove(args.name)
        raise UsageError(f"unknown project command: {cmd}")

    cmd = args.cmd
    name = _command_name(project, cmd)
    if cmd == "init":
        from orch.commands.init import cmd_init

        return name, cmd_init(project)
    if cmd == "worktree-add":
        from orch.commands.worktree_add import cmd_worktree_add

        return name, cmd_worktree_add(project, args.agent, args.branch, base=args.base)
    if cmd == "enqueue":
        from orch.commands.enqueue import cmd_enqueue

        return name, cmd_enqueue(
            project,
            args.agent,
            args.branch,
            args.worktree_path,
            priority=args.priority,
        )
    if cmd == "list":
        from orch.commands.readonly import cmd_list

        return name, cmd_list(project, all_tasks=args.all)
    if cmd == "pending":
        from orch.commands.readonly import cmd_pending

        return name, cmd_pending(project)
    if cmd == "diff":
        from orch.commands.readonly import cmd_diff

        return name, cmd_diff(project, args.task)
    if cmd == "changes":
        from orch.commands.readonly import cmd_changes

        return name, cmd_changes(project, args.task)
    if cmd == "log":
        from orch.commands.readonly import cmd_log

        return name, cmd_log(project, args.task)
    if cmd == "merge":
        from orch.commands.merge import cmd_merge

        return name, cmd_merge(project, once=args.once)
    if cmd == "retry":
        from orch.commands.retry import cmd_retry

        return name, cmd_retry(project, args.task_id)
    if cmd == "skip":
        from orch.commands.skip import cmd_skip

        return name, cmd_skip(project, args.task_id, reason=args.reason)
    if cmd == "reset-stuck":
        from orch.commands.reset_stuck import cmd_reset_stuck

        return name, cmd_reset_stuck(project)
    if cmd == "cleanup":
        from orch.commands.cleanup import cmd_cleanup

        return name, cmd_cleanup(project, prune=args.prune)
    if cmd == "lock-status":
        from orch.commands.lock import cmd_lock_status

        return name, cmd_lock_status(project)
    if cmd == "lock-break":
        from orch.commands.lock import cmd_lock_break

        return name, cmd_lock_break(project, force=args.force)
    if cmd == "agent-list":
        from orch.commands.agent_readonly import cmd_agent_list

        return name, cmd_agent_list(
            project,
            all_runs=bool(getattr(args, "all", False)),
            base_url=getattr(args, "base_url", None),
        )
    if cmd == "agent-show":
        from orch.commands.agent_readonly import cmd_agent_show

        return name, cmd_agent_show(
            project, args.run_id, base_url=getattr(args, "base_url", None)
        )
    if cmd == "agent-watch":
        from orch.commands.agent_readonly import (
            build_observe_adapter,
            cmd_agent_watch,
        )

        adapter = None
        base_url = getattr(args, "base_url", None)
        if base_url:
            adapter = build_observe_adapter(
                base_url,
                username=getattr(args, "username", None),
                password=getattr(args, "password", None),
            )
        # JSONL stream handled by caller when --json; here return a marker.
        return name, {
            "__agent_watch__": True,
            "project": project,
            "run_id": args.run_id,
            "base_url": base_url,
            "interval": float(getattr(args, "interval", 1.0)),
            "ticks": int(getattr(args, "ticks", 1)),
            "adapter": adapter,
        }
    if cmd == "agent-register":
        from orch.commands.agent_readonly import cmd_agent_register

        return name, cmd_agent_register(
            project,
            agent=args.agent,
            branch=args.branch,
            worktree_path=args.worktree_path,
            session_id=args.session,
            runtime_server_id=getattr(args, "server_id", "external"),
            base_url=getattr(args, "base_url", None),
        )
    if cmd == "agent-start":
        from orch.commands.agent_lifecycle import cmd_agent_start

        return name, cmd_agent_start(
            project,
            args.agent,
            args.branch,
            args.worktree_path,
            prompt=getattr(args, "prompt", None),
            prompt_file=getattr(args, "prompt_file", None),
            session_id=getattr(args, "session", None),
        )
    if cmd == "agent-stop":
        from orch.commands.agent_lifecycle import cmd_agent_stop

        return name, cmd_agent_stop(
            project,
            args.run_id,
            force_after=float(getattr(args, "force_after", 5.0)),
        )
    if cmd == "agent-reconcile":
        from orch.commands.agent_lifecycle import cmd_agent_reconcile

        return name, cmd_agent_reconcile(project, getattr(args, "run_id", None))
    if cmd == "agent-archive":
        from orch.commands.agent_lifecycle import cmd_agent_archive

        return name, cmd_agent_archive(project, args.run_id)
    if cmd == "agent-takeover":
        from orch.commands.agent_lifecycle import cmd_agent_takeover

        return name, cmd_agent_takeover(
            project,
            args.run_id,
            fork=bool(getattr(args, "fork", False)),
            launch=bool(getattr(args, "launch", False)),
        )
    if cmd == "agent-release":
        from orch.commands.agent_lifecycle import cmd_agent_release

        return name, cmd_agent_release(
            project,
            args.run_id,
            token=args.token,
            resume=bool(getattr(args, "resume", False)),
            launch=bool(getattr(args, "launch", False)),
        )
    if cmd == "agent-open":
        from orch.commands.agent_lifecycle import cmd_agent_open

        return name, cmd_agent_open(
            project,
            args.run_id,
            fork=bool(getattr(args, "fork", False)),
            launch=bool(getattr(args, "launch", False)),
        )
    if cmd == "coordinator-bind":
        from orch.commands.topic import coordinator_bind

        return name, coordinator_bind(
            project,
            session_id=args.session,
            directory=args.directory,
            runtime_server_id=getattr(args, "server_id", None),
            replace=bool(getattr(args, "replace", False)),
        )
    if cmd == "coordinator-show":
        from orch.commands.topic import coordinator_show

        return name, coordinator_show(project)
    if cmd == "topic-start":
        from orch.commands.topic import topic_start

        return name, topic_start(
            project,
            name=args.name,
            title=args.title,
            goal=args.goal,
            branch_name=args.branch,
            worktree_path=args.worktree,
        )
    if cmd == "topic-list":
        from orch.commands.topic import topic_list

        return name, topic_list(project, include_archived=bool(getattr(args, "all", False)))
    if cmd == "topic-show":
        from orch.commands.topic import topic_show

        return name, topic_show(project, args.topic_id)
    if cmd == "topic-open":
        from orch.commands.topic import topic_open

        return name, topic_open(
            project,
            args.topic_id,
            fork=bool(getattr(args, "fork", False)),
            launch=bool(getattr(args, "launch", False)),
        )
    if cmd == "topic-ready":
        from orch.commands.topic import topic_ready

        return name, topic_ready(
            project,
            args.topic_id,
            verification={
                "commit_sha": args.commit,
                "commands": list(getattr(args, "command", []) or []),
            },
        )
    if cmd == "topic-archive":
        from orch.commands.topic import topic_archive

        return name, topic_archive(project, args.topic_id)
    if cmd == "remote-config":
        from orch.commands.remote import cmd_remote_config

        return name, cmd_remote_config(
            project,
            remote=args.remote,
            provider=args.provider,
            repository=args.repository,
            api_base_url=getattr(args, "api_base_url", "https://api.github.com"),
            integration=args.integration,
            stable=args.stable,
            required_checks=getattr(args, "required_checks", None),
            required_approvals=int(getattr(args, "required_approvals", 1)),
        )
    if cmd == "remote-probe":
        from orch.commands.remote import cmd_remote_probe

        return name, cmd_remote_probe(
            project,
            fetch=not bool(getattr(args, "no_fetch", False)),
        )
    if cmd == "remote-status":
        from orch.commands.remote import cmd_remote_status

        return name, cmd_remote_status(
            project,
            fetch=not bool(getattr(args, "no_fetch", False)),
        )
    if cmd == "promote-develop":
        from orch.commands.promotion import cmd_promote_develop

        return name, cmd_promote_develop(
            project,
            execute=bool(getattr(args, "execute", False)),
            verification=getattr(args, "verification", None),
            no_fetch=bool(getattr(args, "no_fetch", False)),
        )
    if cmd == "promotion-list":
        from orch.commands.promotion import cmd_promotion_list

        return name, cmd_promotion_list(
            project,
            kind=getattr(args, "kind", None),
            limit=int(getattr(args, "limit", 50)),
        )
    if cmd == "promotion-show":
        from orch.commands.promotion import cmd_promotion_show

        return name, cmd_promotion_show(project, args.promotion_id)
    if cmd == "promotion-reconcile":
        from orch.commands.promotion import cmd_promotion_reconcile

        return name, cmd_promotion_reconcile(
            project,
            args.promotion_id,
            no_fetch=bool(getattr(args, "no_fetch", False)),
        )
    if cmd == "promotion-cancel":
        from orch.commands.promotion import cmd_promotion_cancel

        return name, cmd_promotion_cancel(
            project,
            args.promotion_id,
            reason=str(args.reason),
            actor=str(getattr(args, "actor", "operator")),
            no_fetch=bool(getattr(args, "no_fetch", False)),
        )
    if cmd == "release-create":
        from orch.commands.promotion import cmd_release_create

        return name, cmd_release_create(
            project,
            verification=str(args.verification),
            title=getattr(args, "title", None),
            execute=bool(getattr(args, "execute", False)),
            no_fetch=bool(getattr(args, "no_fetch", False)),
        )
    if cmd == "release-status":
        from orch.commands.promotion import cmd_release_status

        return name, cmd_release_status(
            project,
            args.promotion_id,
            no_fetch=bool(getattr(args, "no_fetch", False)),
        )
    if cmd == "release-sync":
        from orch.commands.promotion import cmd_release_sync

        return name, cmd_release_sync(
            project,
            args.promotion_id,
            execute=bool(getattr(args, "execute", False)),
            no_fetch=bool(getattr(args, "no_fetch", False)),
        )
    raise UsageError(f"unknown command: {cmd}")


def _wants_json(argv: Sequence[str]) -> bool:
    return "--json" in argv


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    as_json = _wants_json(argv)

    if not argv or argv[0] in ("-h", "--help"):
        print(
            "usage: orch project {list,add,remove} | orch runtime {probe,start,status,stop}\n"
            "       orch <project> <command> [...]\n"
            "       orch --version\n"
            "Try: orch project --help | orch runtime --help | orch <project> --help",
            file=sys.stderr if False else sys.stdout,
        )
        return ExitCode.SUCCESS

    if argv[0] == "--version":
        print(f"orch {__version__}")
        return ExitCode.SUCCESS

    # Strip global-only --json for routing; parsers still accept it.
    command_name = "orch"
    try:
        if argv[0] == "project":
            parser = _parser_project_group()
            args = parser.parse_args(argv[1:])
            # propagate --json from either level
            as_json = as_json or getattr(args, "json", False)
            command_name, data = _dispatch(args, project=None)
        elif argv[0] == "runtime":
            parser = _parser_runtime_group()
            args = parser.parse_args(argv[1:])
            as_json = as_json or getattr(args, "json", False)
            command_name, data = _dispatch_runtime(args)
            if as_json:
                emit_json(success_envelope(command_name, data))
            else:
                _print_human(command_name, data)
            # probe may conclude phase0_pass=false without raising; still exit 0
            # unless a hard OrchError occurred. Callers inspect data.phase0_pass.
            return ExitCode.SUCCESS
        else:
            project = argv[0]
            if project.startswith("-"):
                raise UsageError(f"unknown option: {project}")
            rest = argv[1:]
            if not rest or rest[0] in ("-h", "--help"):
                _parser_for_project(project).print_help()
                return ExitCode.SUCCESS
            if rest[0] not in PROJECT_COMMANDS and not rest[0].startswith("-"):
                raise UsageError(
                    f"unknown command: {rest[0]}",
                    details={"project": project},
                )
            parser = _parser_for_project(project)
            args = parser.parse_args(rest)
            as_json = as_json or getattr(args, "json", False)
            command_name, data = _dispatch(args, project=project)

            # agent-watch --json uses JSONL stream (not a single envelope).
            if (
                isinstance(data, dict)
                and data.get("__agent_watch__")
            ):
                from orch.commands.agent_readonly import cmd_agent_watch

                if as_json:
                    cmd_agent_watch(
                        data["project"],
                        data["run_id"],
                        base_url=data.get("base_url"),
                        interval_sec=float(data.get("interval") or 1.0),
                        max_ticks=int(data.get("ticks") or 1),
                        as_jsonl=True,
                        adapter=data.get("adapter"),
                    )
                    return ExitCode.SUCCESS
                snap = cmd_agent_watch(
                    data["project"],
                    data["run_id"],
                    base_url=data.get("base_url"),
                    interval_sec=float(data.get("interval") or 1.0),
                    max_ticks=int(data.get("ticks") or 1),
                    as_jsonl=False,
                    adapter=data.get("adapter"),
                )
                data = snap

        if as_json:
            emit_json(success_envelope(command_name, data))
        else:
            _print_human(command_name, data)
        return ExitCode.SUCCESS
    except SystemExit as exc:
        # argparse errors
        code = int(exc.code) if exc.code is not None else ExitCode.USAGE
        if code == 0:
            return 0
        if as_json:
            emit_json(
                envelope_from_exception(
                    command_name,
                    UsageError("invalid arguments", details={"argv": list(argv)}),
                )
            )
        return ExitCode.USAGE
    except OrchError as exc:
        if as_json:
            emit_json(envelope_from_exception(command_name, exc))
        else:
            print(f"error: {exc.message}", file=sys.stderr)
            if exc.details:
                print(f"details: {exc.details}", file=sys.stderr)
        return exc.code
    except KeyboardInterrupt:
        if as_json:
            from orch.errors import InterruptedMergeError

            emit_json(envelope_from_exception(command_name, InterruptedMergeError()))
        else:
            print("interrupted", file=sys.stderr)
        return ExitCode.INTERRUPTED
    except Exception as exc:  # noqa: BLE001
        if as_json:
            emit_json(envelope_from_exception(command_name, exc))
        else:
            print(f"error: {exc}", file=sys.stderr)
            traceback.print_exc()
        return ExitCode.GENERAL


def _print_human(command: str, data: Any) -> None:
    if data is None:
        print(f"{command}: ok")
        return
    if command == "runtime.probe" and isinstance(data, dict):
        _print_probe_human(data)
        return
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, (dict, list)) and k not in ("diff", "log", "diff_stat"):
                print(f"{k}:")
                print(v)
            else:
                print(f"{k}: {v}")
        return
    print(data)


def _print_probe_human(data: dict[str, Any]) -> None:
    print(f"phase0_pass: {data.get('phase0_pass')}")
    print(f"opencode_cli_version: {data.get('opencode_cli_version')}")
    print(f"opencode_server_version: {data.get('opencode_server_version')}")
    print(f"base_url: {data.get('base_url')}")
    print(f"supported_min_version: {data.get('supported_min_version')}")
    print(f"architecture_decision: {data.get('architecture_decision')}")
    print(f"architecture_note: {data.get('architecture_note')}")
    print("capabilities:")
    caps = data.get("capabilities") or {}
    if isinstance(caps, dict):
        for k, v in caps.items():
            print(f"  {k}: {v}")
    print("hypotheses:")
    for hyp in data.get("hypotheses") or []:
        if isinstance(hyp, dict):
            print(
                f"  {hyp.get('id')} [{hyp.get('risk')}] {hyp.get('status')}: "
                f"{hyp.get('statement')}"
            )
    print("checks:")
    for chk in data.get("checks") or []:
        if isinstance(chk, dict):
            mark = "PASS" if chk.get("ok") else "FAIL"
            print(f"  [{mark}] {chk.get('name')}: {chk.get('detail')}")
    attach = data.get("attach_commands") or {}
    if isinstance(attach, dict) and attach.get("session_a"):
        print(f"attach: {attach.get('session_a')}")
        print(f"attach_fork: {attach.get('session_a_fork')}")
    desk = data.get("desktop_acceptance") or {}
    if isinstance(desk, dict):
        print(f"desktop_acceptance: {desk.get('status')}")
        for step in desk.get("steps") or []:
            print(f"  - {step}")
