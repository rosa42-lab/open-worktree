"""orch <project> cleanup [--prune]."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from orch.audit import write_audit
from orch.constants import (
    BARE_DIR_NAME,
    CLEANUP_COOLDOWN_HOURS,
    TARGET_BRANCH,
    project_lock_path,
)
from orch.db import immediate_transaction, open_project_db
from orch.git.ref import run_git_ref
from orch.git.worktree import (
    assert_worktree_owns_bare,
    run_git_worktree,
    worktree_list_porcelain,
)
from orch.locks import acquire, release
from orch.registry import get_project_path
from orch.runtime.cleanup_guard import runtime_prune_blockers
from orch.runtime.hooks import load_hooks_config, run_hook
from orch.task_resolve import row_to_dict
from orch.util import utc_now_iso
from orch.validate import validate_project_name


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def _candidates(conn) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM tasks
        WHERE status = 'merged' AND archived_at IS NULL
        ORDER BY finished_at ASC
        """
    ).fetchall()
    now = datetime.now(timezone.utc)
    out = []
    for row in rows:
        d = row_to_dict(row)
        finished = _parse_iso(row["finished_at"])
        cooled = True
        if finished is not None:
            if finished.tzinfo is None:
                finished = finished.replace(tzinfo=timezone.utc)
            cooled = now - finished >= timedelta(hours=CLEANUP_COOLDOWN_HOURS)
        d["cooldown_elapsed"] = cooled
        d["prunable"] = cooled
        out.append(d)
    return out


def cmd_cleanup(project: str, *, prune: bool = False) -> dict[str, Any]:
    project = validate_project_name(project)
    root = get_project_path(project)
    bare = (root / BARE_DIR_NAME).resolve()
    conn = open_project_db(project, init=True)
    if not prune:
        try:
            cands = _candidates(conn)
            return {"candidates": cands, "prune": False}
        finally:
            conn.close()

    handle = None
    try:
        handle = acquire(
            project_lock_path(project),
            command="cleanup",
            project=project,
            audit_conn=conn,
        )
        cands = _candidates(conn)
        results = []
        for task in cands:
            if not task.get("prunable"):
                results.append(
                    {
                        "task_id": task["id"],
                        "skipped": True,
                        "reason": "cooldown",
                    }
                )
                continue
            results.append(_prune_one(conn, bare, task))
        return {"prune": True, "results": results}
    finally:
        if handle is not None:
            release(handle, audit_conn=conn)
        conn.close()


def _prune_one(conn, bare: Path, task: dict[str, Any]) -> dict[str, Any]:
    wt = Path(task["worktree_path"])
    branch = task["branch_name"]

    # V12-011: runtime guards BEFORE any Git mutation
    blockers = runtime_prune_blockers(
        conn,
        worktree_path=str(wt),
        task_status=task.get("status"),
    )
    if blockers:
        return {
            "task_id": task["id"],
            "ok": False,
            "reason": "runtime_blocked",
            "blockers": blockers,
        }

    # Optional BeforeWorktreeRemove hook (cannot bypass guards above)
    try:
        from orch.config import read_config

        hooks = load_hooks_config(read_config())
    except Exception:  # noqa: BLE001
        hooks = {}
    hook_cfg = hooks.get("BeforeWorktreeRemove") if isinstance(hooks, dict) else None
    if isinstance(hook_cfg, dict) and hook_cfg.get("argv"):
        try:
            run_hook(
                "BeforeWorktreeRemove",
                argv=list(hook_cfg["argv"]),
                payload={
                    "project_task_id": task["id"],
                    "worktree_path": str(wt),
                    "branch": branch,
                },
                timeout_seconds=float(hook_cfg.get("timeout_seconds") or 10),
                blocking=bool(hook_cfg.get("blocking")),
            )
        except Exception as exc:  # noqa: BLE001
            return {
                "task_id": task["id"],
                "ok": False,
                "reason": "hook_blocked",
                "detail": str(exc),
            }

    try:
        assert_worktree_owns_bare(wt, bare)
    except Exception as exc:
        return {"task_id": task["id"], "ok": False, "reason": str(exc)}

    st = run_git_worktree(["status", "--porcelain"], wt)
    if not st.ok or st.stdout.strip():
        return {"task_id": task["id"], "ok": False, "reason": "worktree not clean"}

    entries = worktree_list_porcelain(bare)
    matches = [
        e
        for e in entries
        if "worktree" in e and Path(e["worktree"]).resolve() == wt.resolve()
    ]
    if len(matches) != 1:
        return {
            "task_id": task["id"],
            "ok": False,
            "reason": "worktree not uniquely registered",
        }
    if matches[0].get("locked"):
        return {
            "task_id": task["id"],
            "ok": False,
            "reason": "git worktree is locked; unlock manually",
        }

    # branch must not be checked out elsewhere
    for e in entries:
        if e is matches[0]:
            continue
        b = e.get("branch", "")
        if b.endswith(f"/{branch}") or b == f"refs/heads/{branch}":
            return {
                "task_id": task["id"],
                "ok": False,
                "reason": "branch checked out in another worktree",
                "other": e.get("worktree"),
            }

    tip_r = run_git_ref(["rev-parse", branch], bare)
    if not tip_r.ok:
        return {"task_id": task["id"], "ok": False, "reason": "branch tip missing"}
    branch_tip = tip_r.stdout.strip()

    anc = run_git_ref(
        ["merge-base", "--is-ancestor", branch_tip, TARGET_BRANCH],
        bare,
    )
    if not anc.ok:
        return {
            "task_id": task["id"],
            "ok": False,
            "reason": "branch tip not ancestor of develop",
        }

    rm = run_git_ref(["worktree", "remove", str(wt)], bare)
    if not rm.ok:
        return {
            "task_id": task["id"],
            "ok": False,
            "reason": f"worktree remove failed: {rm.stderr}",
        }

    uref = run_git_ref(
        ["update-ref", "-d", f"refs/heads/{branch}", branch_tip],
        bare,
    )
    if not uref.ok:
        return {
            "task_id": task["id"],
            "ok": False,
            "reason": f"update-ref -d failed: {uref.stderr}",
            "worktree_removed": True,
            "branch_tip": branch_tip,
        }

    run_git_ref(["worktree", "prune"], bare)

    archived = utc_now_iso()
    with immediate_transaction(conn) as c:
        c.execute(
            "UPDATE tasks SET archived_at = ? WHERE id = ?",
            (archived, task["id"]),
        )
        write_audit(
            c,
            "cleanup_pruned",
            task_id=task["id"],
            detail={
                "worktree": str(wt),
                "branch": branch,
                "branch_tip": branch_tip,
            },
        )
    return {
        "task_id": task["id"],
        "ok": True,
        "archived_at": archived,
        "branch_deleted": branch,
    }
