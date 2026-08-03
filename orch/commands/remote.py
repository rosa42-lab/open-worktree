"""remote-config / remote-probe / remote-status（V13-001/002/004）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from orch.constants import BARE_DIR_NAME, TARGET_BRANCH
from orch.errors import ValidationError
from orch.promotion.config import (
    get_promotion_config,
    validate_promotion_fields,
    write_promotion_config,
)
from orch.registry import get_project_path
from orch.remote.git import CliRemoteGitAdapter, classify_ref_relation
from orch.remote.probe import STABLE_BRANCH, run_remote_probe
from orch.validate import validate_project_name


def cmd_remote_config(
    project: str,
    *,
    remote: str,
    provider: str,
    repository: str,
    api_base_url: str = "https://api.github.com",
    integration: str = "develop",
    stable: str = "master",
    required_checks: list[str] | None = None,
    required_approvals: int = 1,
) -> dict[str, Any]:
    project = validate_project_name(project)
    entry = {
        "remote": remote,
        "provider": provider,
        "repository": repository,
        "api_base_url": api_base_url,
        "integration_branch": integration,
        "stable_branch": stable,
        "release_merge_method": "merge_commit",
        "freeze_develop_during_release": True,
        "freeze_local_merge_queue_during_release": True,
        "required_checks": required_checks
        or ["test", "build", "promotion-policy"],
        "required_approvals": required_approvals,
    }
    # 先格式校验再写（write 内也会校验 + remote URL）
    validate_promotion_fields(entry)
    saved = write_promotion_config(project, entry)
    return {
        "project": project,
        "promotion": saved,
        "capabilities_verified": False,
        "note": "format validated only; run remote-probe for platform capabilities",
    }


def cmd_remote_probe(project: str, *, fetch: bool = True) -> dict[str, Any]:
    project = validate_project_name(project)
    return run_remote_probe(project, fetch=fetch)


def cmd_remote_status(project: str, *, fetch: bool = True) -> dict[str, Any]:
    project = validate_project_name(project)
    root = get_project_path(project)
    bare = root / BARE_DIR_NAME
    if not bare.is_dir():
        raise ValidationError(
            f".bare.git missing under {root}",
            kind="bare_missing",
            details={"path": str(root)},
        )

    promo = get_promotion_config(project)
    remote = (promo or {}).get("remote", "origin")
    develop = (promo or {}).get("integration_branch", TARGET_BRANCH)
    master = (promo or {}).get("stable_branch", STABLE_BRANCH)
    adapter = CliRemoteGitAdapter()

    fetched = False
    fetch_error = None
    if fetch:
        try:
            adapter.fetch_core_refs(bare, remote, develop, master)
            fetched = True
        except Exception as exc:  # noqa: BLE001
            fetch_error = str(exc)[:400]

    local_develop = adapter.local_head(bare, develop)
    remote_develop = adapter.remote_head(bare, remote, develop)
    remote_master = adapter.remote_head(bare, remote, master)
    # schema 3 落地前无成功 promotion 记录
    last_successful_promotion_sha = None

    relation = classify_ref_relation(adapter, bare, local_develop, remote_develop)

    return {
        "project": project,
        "remote": remote,
        "integration_branch": develop,
        "stable_branch": master,
        "fetched": fetched,
        "fetch_error": fetch_error,
        "local_develop_sha": local_develop,
        "remote_develop_sha": remote_develop,
        "remote_master_sha": remote_master,
        "last_successful_promotion_sha": last_successful_promotion_sha,
        "develop_relation": relation,
        "write_performed": False,
    }
