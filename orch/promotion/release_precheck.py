"""master release precheck（设计 §10.1）。"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from orch.constants import BARE_DIR_NAME, TARGET_BRANCH
from orch.errors import ValidationError
from orch.promotion.config import get_promotion_config
from orch.promotion.precheck import PrecheckBlocked, PrecheckManual, PrecheckRetryable
from orch.promotion.repo import find_active
from orch.remote.git import CliRemoteGitAdapter
from orch.verification.service import require_passed_verification


def run_release_precheck(
    conn: sqlite3.Connection,
    project: str,
    root: Path,
    *,
    verification_record_id: str | None,
    adapter: CliRemoteGitAdapter | None = None,
    fetch: bool = True,
) -> dict[str, Any]:
    promo = get_promotion_config(project)
    if not promo:
        raise PrecheckBlocked(
            "promotion config missing; run remote-config first",
            details={"field": "promotion"},
        )
    if str(promo.get("release_merge_method") or "") != "merge_commit":
        raise PrecheckBlocked(
            "release_merge_method must be merge_commit",
            details={"release_merge_method": promo.get("release_merge_method")},
        )
    if str(promo.get("provider") or "") != "github":
        raise PrecheckBlocked(
            "release-create requires provider=github in v1.3",
            details={"provider": promo.get("provider")},
        )
    repository = str(promo.get("repository") or "")
    if not repository or "/" not in repository:
        raise PrecheckBlocked("repository must be owner/name", details={"repository": repository})

    remote = str(promo.get("remote") or "origin")
    develop = str(promo.get("integration_branch") or TARGET_BRANCH)
    master = str(promo.get("stable_branch") or "master")
    bare = root / BARE_DIR_NAME
    if not bare.is_dir():
        raise PrecheckBlocked(".bare.git missing", details={"path": str(bare)})

    adapter = adapter or CliRemoteGitAdapter()
    if fetch:
        try:
            adapter.fetch_core_refs(bare, remote, develop, master)
        except Exception as exc:  # noqa: BLE001
            raise PrecheckRetryable(
                f"fetch failed: {exc}",
                details={"error": str(exc)[:400]},
            ) from exc

    remote_develop = adapter.remote_head(bare, remote, develop)
    remote_master = adapter.remote_head(bare, remote, master)
    local_develop = adapter.local_head(bare, develop)
    if not remote_develop or not remote_master:
        raise PrecheckBlocked(
            "origin/develop and origin/master must both exist",
            details={
                "remote_develop": remote_develop,
                "remote_master": remote_master,
            },
        )
    if not local_develop:
        raise PrecheckBlocked("local develop missing", details={"branch": develop})

    # master 必须是 develop 祖先
    if not adapter.is_ancestor(bare, remote_master, remote_develop):
        raise PrecheckBlocked(
            "origin/master is not an ancestor of origin/develop",
            details={"master": remote_master, "develop": remote_develop},
        )

    if find_active(conn, project, "master_release") is not None:
        active = find_active(conn, project, "master_release")
        raise PrecheckBlocked(
            "another active master_release exists",
            details={"active_id": (active or {}).get("id")},
        )
    if find_active(conn, project, "develop_publish") is not None:
        active = find_active(conn, project, "develop_publish")
        raise PrecheckBlocked(
            "active develop_publish blocks release-create",
            details={"active_id": (active or {}).get("id")},
        )

    # local == remote develop == source
    if local_develop != remote_develop:
        raise PrecheckBlocked(
            "local develop must match remote develop tip before release",
            details={"local": local_develop, "remote": remote_develop},
        )

    source_sha = remote_develop
    if not verification_record_id:
        raise PrecheckBlocked(
            "--verification is required",
            details={"flag": "--verification"},
        )
    try:
        require_passed_verification(
            conn,
            project,
            source_sha,
            scope="master_release",
            required_commands=None,
        )
    except ValidationError as exc:
        # also accept develop_publish verification bound to same tip (practical)
        try:
            require_passed_verification(
                conn,
                project,
                source_sha,
                scope="develop_publish",
                required_commands=None,
            )
        except ValidationError:
            raise PrecheckBlocked(
                f"verification failed for remote develop tip: {exc}",
                details={"commit_sha": source_sha, "scope": "master_release|develop_publish"},
            ) from exc

    # explicit record id must match tip
    row = conn.execute(
        "SELECT * FROM verification_records WHERE id = ?",
        (verification_record_id,),
    ).fetchone()
    if row is None:
        raise PrecheckBlocked(
            f"verification record not found: {verification_record_id}",
            details={"id": verification_record_id},
        )
    rec = dict(row)
    if rec.get("commit_sha") != source_sha:
        raise PrecheckBlocked(
            "verification commit_sha must equal remote develop tip",
            details={
                "verification_sha": rec.get("commit_sha"),
                "source_sha": source_sha,
            },
        )
    if rec.get("status") != "passed":
        raise PrecheckBlocked(
            "verification status must be passed",
            details={"status": rec.get("status")},
        )

    return {
        "remote": remote,
        "develop": develop,
        "master": master,
        "repository": repository,
        "api_base_url": str(promo.get("api_base_url") or "https://api.github.com"),
        "provider": "github",
        "source_sha": source_sha,
        "target_sha_before": remote_master,
        "verification_record_id": verification_record_id,
        "required_checks": list(promo.get("required_checks") or []),
        "required_approvals": int(promo.get("required_approvals") or 1),
    }
