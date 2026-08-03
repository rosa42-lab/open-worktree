"""promote-develop precheck（设计 §9.1，12 项）。"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from orch.constants import BARE_DIR_NAME, MAIN_WORKTREE_NAME, TARGET_BRANCH
from orch.errors import ValidationError
from orch.git.worktree import assert_worktree_owns_bare, run_git_worktree
from orch.promotion.config import get_promotion_config
from orch.promotion.repo import find_active
from orch.remote.git import CliRemoteGitAdapter
from orch.verification.service import require_passed_verification


class PrecheckBlocked(ValidationError):
    """策略/ancestry 类失败 → 映射到 blocked。"""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message,
            kind="promotion_precheck_blocked",
            details=details or {},
        )


class PrecheckRetryable(ValidationError):
    """远端不可用等 → failed_safe_to_retry。"""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message,
            kind="promotion_precheck_retryable",
            details=details or {},
        )


class PrecheckManual(ValidationError):
    """证据不足 → manual_required。"""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message,
            kind="promotion_precheck_manual",
            details=details or {},
        )


def _blocking_queue_tasks(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, status, branch_name FROM tasks
        WHERE status IN ('merging','conflict','recovery_required')
        ORDER BY queue_seq
        """
    ).fetchall()
    return [dict(r) for r in rows]


def _merged_tasks_in_range(
    conn: sqlite3.Connection,
    adapter: CliRemoteGitAdapter,
    bare: Path,
    old_sha: str,
    new_sha: str,
) -> list[dict[str, str]]:
    rows = conn.execute(
        """
        SELECT id, merged_commit FROM tasks
        WHERE status = 'merged' AND merged_commit IS NOT NULL AND merged_commit != ''
        """
    ).fetchall()
    out: list[dict[str, str]] = []
    for row in rows:
        tid = str(row["id"])
        mc = str(row["merged_commit"])
        if mc == old_sha:
            continue
        # mc 在 (old, new]：old 是 mc 祖先（或 mc 可达自 old），且 mc 是 new 的祖先或相等
        on_new = mc == new_sha or adapter.is_ancestor(bare, mc, new_sha)
        after_old = adapter.is_ancestor(bare, old_sha, mc)
        if on_new and after_old:
            out.append({"task_id": tid, "merged_commit": mc})
    return out


def run_develop_precheck(
    conn: sqlite3.Connection,
    project: str,
    root: Path,
    *,
    verification_record_id: str | None,
    adapter: CliRemoteGitAdapter | None = None,
    fetch: bool = True,
) -> dict[str, Any]:
    """
    返回冻结计划；失败抛 PrecheckBlocked / PrecheckRetryable / PrecheckManual / ValidationError。
    """
    adapter = adapter or CliRemoteGitAdapter()
    bare = root / BARE_DIR_NAME
    main = root / MAIN_WORKTREE_NAME

    # 1) bare / main / local develop
    if not bare.is_dir():
        raise PrecheckBlocked("bare repository missing", details={"path": str(bare)})
    if not main.is_dir():
        raise PrecheckBlocked("integration worktree main/ missing", details={"path": str(main)})
    try:
        assert_worktree_owns_bare(main, bare)
    except Exception as exc:  # noqa: BLE001
        raise PrecheckBlocked(
            "main/ does not belong to project bare",
            details={"error": str(exc)[:200]},
        ) from exc

    br = run_git_worktree(["rev-parse", "--abbrev-ref", "HEAD"], main)
    if not br.ok or br.stdout.strip() != TARGET_BRANCH:
        raise PrecheckBlocked(
            f"main/ not on {TARGET_BRANCH}",
            details={"branch": (br.stdout or "").strip()},
        )

    # 2) clean + no MERGE_HEAD
    st = run_git_worktree(["status", "--porcelain"], main)
    if not st.ok:
        raise PrecheckRetryable("cannot read main/ status", details={"stderr": st.stderr[:200]})
    if st.stdout.strip():
        raise PrecheckBlocked("main/ is not clean", details={"porcelain": st.stdout.strip()[:400]})
    mh = run_git_worktree(["rev-parse", "--git-path", "MERGE_HEAD"], main, check=True)
    mh_path = Path(mh.stdout.strip())
    if not mh_path.is_absolute():
        mh_path = main / mh_path
    if mh_path.exists():
        raise PrecheckBlocked("main/ has MERGE_HEAD (merge in progress)")

    # 3) blocking queue tasks
    blocking = _blocking_queue_tasks(conn)
    if blocking:
        raise PrecheckBlocked(
            "queue has merging/conflict/recovery_required tasks",
            details={"tasks": blocking},
        )

    # 4) active master release freeze
    active_release = find_active(conn, project, "master_release")
    if active_release is not None:
        raise PrecheckBlocked(
            "active master_release freezes promote-develop",
            details={"promotion_id": active_release["id"], "state": active_release["state"]},
        )

    # 5) remote/provider config
    promo = get_promotion_config(project)
    if promo is None:
        raise PrecheckBlocked(
            "promotion config missing; run remote-config first",
            details={"project": project},
        )
    remote = str(promo["remote"])
    develop = str(promo["integration_branch"])
    master = str(promo["stable_branch"])
    mode = "direct_ff"  # Phase 0 冻结；candidate_pr 另开路径
    if mode != "direct_ff":
        raise PrecheckBlocked(
            f"develop publish mode {mode!r} not supported for CAS path",
            details={"mode": mode},
        )

    # 6) fetch + SHAs
    if fetch:
        try:
            adapter.fetch_core_refs(bare, remote, develop, master)
        except Exception as exc:  # noqa: BLE001
            raise PrecheckRetryable(
                "fetch core refs failed",
                details={"error": str(exc)[:400]},
            ) from exc

    local_sha = adapter.local_head(bare, develop)
    remote_sha = adapter.remote_head(bare, remote, develop)
    remote_master = adapter.remote_head(bare, remote, master)
    if not local_sha:
        raise PrecheckBlocked("local develop SHA undetermined")
    if not remote_sha:
        raise PrecheckRetryable("remote develop SHA undetermined")
    if not remote_master:
        raise PrecheckRetryable("remote master SHA undetermined")

    # 7) remote develop is ancestor of local
    if remote_sha != local_sha and not adapter.is_ancestor(bare, remote_sha, local_sha):
        raise PrecheckBlocked(
            "remote develop is not an ancestor of local develop (diverged or remote_ahead)",
            details={
                "local_develop_sha": local_sha,
                "remote_develop_sha": remote_sha,
            },
        )

    # 8) must have something to publish
    if local_sha == remote_sha:
        raise PrecheckBlocked(
            "local develop equals remote develop; nothing to promote",
            details={"sha": local_sha},
        )

    # 9–10) aggregate verification on source_sha
    try:
        gate = require_passed_verification(
            conn,
            project,
            local_sha,
            scope="develop_publish",
            required_commands=None,
        )
    except ValidationError as exc:
        if getattr(exc, "kind", None) == "verification_required":
            raise PrecheckBlocked(str(exc), details=getattr(exc, "details", None) or {}) from exc
        raise
    if verification_record_id and gate["id"] != verification_record_id:
        from orch.verification import repo as vrepo

        rec = vrepo.get_by_id(conn, verification_record_id)
        if (
            rec is None
            or rec.get("commit_sha") != local_sha
            or rec.get("status") != "passed"
            or rec.get("scope") != "develop_publish"
        ):
            raise PrecheckBlocked(
                "verification record does not match source_sha / develop_publish / passed",
                details={"verification_record_id": verification_record_id},
            )
        gate = rec

    # 11) direct_ff CAS capability — adapter 提供 push_fast_forward
    if not hasattr(adapter, "push_fast_forward"):
        raise PrecheckManual("adapter cannot perform CAS fast-forward")

    # 12) Bot / policy — Phase 0 已证明；此处记录假设，完整 probe 留 V13-009
    policy_note = "phase0_direct_ff_assumed; full provider probe in V13-009"

    included = _merged_tasks_in_range(conn, adapter, bare, remote_sha, local_sha)

    return {
        "project": project,
        "mode": mode,
        "remote": remote,
        "provider": promo["provider"],
        "repository": promo["repository"],
        "source_ref": f"refs/heads/{develop}",
        "target_ref": f"refs/heads/{develop}",
        "source_sha": local_sha,
        "target_sha_before": remote_sha,
        "remote_master_sha": remote_master,
        "verification_record_id": gate["id"],
        "included_tasks": included,
        "policy_note": policy_note,
        "write_planned": True,
    }
