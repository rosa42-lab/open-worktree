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
        description="Multi-agent worktree orchestrator (v1.1)",
    )
    parser.add_argument("--version", action="version", version=f"orch {__version__}")
    _add_json(parser)

    # We use a two-phase parse: first token may be "project" or <project-name>.
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
    }
)


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
    return p


def _command_name(project: str | None, cmd: str) -> str:
    if project:
        return f"{project}.{cmd}"
    return f"project.{cmd}"


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
    raise UsageError(f"unknown command: {cmd}")


def _wants_json(argv: Sequence[str]) -> bool:
    return "--json" in argv


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    as_json = _wants_json(argv)

    if not argv or argv[0] in ("-h", "--help"):
        print(
            "usage: orch project {list,add,remove} | orch <project> <command> [...]\n"
            "       orch --version\n"
            "Try: orch project --help   or   orch <project> --help",
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
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, (dict, list)) and k not in ("diff", "log", "diff_stat"):
                print(f"{k}:")
                print(v)
            else:
                print(f"{k}: {v}")
        return
    print(data)
