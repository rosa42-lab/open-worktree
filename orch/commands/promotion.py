"""promotion / release CLI（V13-007…011）。"""

from __future__ import annotations

from typing import Any

from orch.promotion.reconcile import (
    promotion_cancel,
    promotion_list,
    promotion_reconcile,
    promotion_show,
)
from orch.promotion.release_service import release_create, release_status
from orch.promotion.release_sync import release_sync
from orch.promotion.service import promote_develop
from orch.validate import validate_project_name


def cmd_promote_develop(
    project: str,
    *,
    execute: bool = False,
    verification: str | None = None,
    no_fetch: bool = False,
) -> dict[str, Any]:
    project = validate_project_name(project)
    return promote_develop(
        project,
        execute=execute,
        verification_record_id=verification,
        fetch=not no_fetch,
    )


def cmd_promotion_list(
    project: str,
    *,
    kind: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    return promotion_list(project, kind=kind, limit=limit)


def cmd_promotion_show(project: str, promotion_id: str) -> dict[str, Any]:
    return promotion_show(project, promotion_id)


def cmd_promotion_reconcile(
    project: str,
    promotion_id: str,
    *,
    no_fetch: bool = False,
) -> dict[str, Any]:
    return promotion_reconcile(project, promotion_id, fetch=not no_fetch)


def cmd_promotion_cancel(
    project: str,
    promotion_id: str,
    *,
    reason: str,
    actor: str = "operator",
    no_fetch: bool = False,
) -> dict[str, Any]:
    return promotion_cancel(
        project,
        promotion_id,
        reason=reason,
        actor=actor,
        fetch=not no_fetch,
    )


def cmd_release_create(
    project: str,
    *,
    verification: str,
    title: str | None = None,
    execute: bool = False,
    no_fetch: bool = False,
) -> dict[str, Any]:
    project = validate_project_name(project)
    return release_create(
        project,
        verification=verification,
        title=title,
        execute=execute,
        fetch=not no_fetch,
    )


def cmd_release_status(
    project: str,
    promotion_id: str,
    *,
    no_fetch: bool = False,
) -> dict[str, Any]:
    project = validate_project_name(project)
    return release_status(project, promotion_id, fetch=not no_fetch)


def cmd_release_sync(
    project: str,
    promotion_id: str,
    *,
    execute: bool = False,
    no_fetch: bool = False,
) -> dict[str, Any]:
    project = validate_project_name(project)
    return release_sync(
        project,
        promotion_id,
        execute=execute,
        fetch=not no_fetch,
    )
