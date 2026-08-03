"""promotion 配置读写与校验（V13-002 / 设计 §7）。"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from orch.config import read_config, write_config_atomic
from orch.constants import BARE_DIR_NAME
from orch.errors import ExitCode, OrchError, UsageError, ValidationError
from orch.git.ref import run_git_ref
from orch.registry import get_project_path

ALLOWED_PROVIDERS = frozenset({"github", "gitlab", "manual"})
FORBIDDEN_SECRET_KEYS = frozenset(
    {
        "token",
        "password",
        "secret",
        "credential",
        "credentials",
        "authorization",
        "api_key",
        "apikey",
        "access_token",
        "private_key",
        "client_secret",
    }
)

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def default_promotion_entry() -> dict[str, Any]:
    return {
        "remote": "origin",
        "provider": "github",
        "repository": "",
        "api_base_url": "https://api.github.com",
        "integration_branch": "develop",
        "stable_branch": "master",
        "release_merge_method": "merge_commit",
        "freeze_develop_during_release": True,
        "freeze_local_merge_queue_during_release": True,
        "required_checks": ["test", "build", "promotion-policy"],
        "required_approvals": 1,
    }


def get_promotion_config(project: str) -> dict[str, Any] | None:
    cfg = read_config()
    promo = cfg.get("promotion")
    if not isinstance(promo, dict):
        return None
    entry = promo.get(project)
    if not isinstance(entry, dict):
        return None
    return dict(entry)


def parse_github_repository_identity(remote_url: str) -> str | None:
    """从 remote URL 解析 owner/repo；非 GitHub 形态返回 None。"""
    url = remote_url.strip()
    if not url:
        return None
    if url.startswith("git@"):
        # git@github.com:owner/repo.git
        _, _, rest = url.partition(":")
        path = rest
    else:
        parsed = urlparse(url)
        path = parsed.path or ""
        host = (parsed.hostname or "").lower()
        if host and "github" not in host and not host.endswith("githubusercontent.com"):
            # 仍尝试通用 owner/repo 形态（自建可能仿造 path）
            pass
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        return None
    return f"{parts[-2]}/{parts[-1]}"


def _reject_secrets(data: dict[str, Any]) -> None:
    for key in data:
        lowered = str(key).lower().replace("-", "_")
        if lowered in FORBIDDEN_SECRET_KEYS or any(
            s in lowered for s in ("token", "password", "secret", "credential")
        ):
            raise ValidationError(
                f"secret key not allowed in promotion config: {key}",
                kind="promotion_config_invalid",
                details={"key": key},
            )


def validate_promotion_fields(raw: dict[str, Any]) -> dict[str, Any]:
    _reject_secrets(raw)
    out = default_promotion_entry()
    out.update({k: v for k, v in raw.items() if k in out or k == "repository"})

    remote = str(out.get("remote") or "").strip()
    if not remote:
        raise ValidationError(
            "remote is required",
            kind="promotion_config_invalid",
            details={"field": "remote"},
        )
    out["remote"] = remote

    provider = str(out.get("provider") or "").strip().lower()
    if provider not in ALLOWED_PROVIDERS:
        raise ValidationError(
            f"unsupported provider: {provider!r}",
            kind="promotion_config_invalid",
            details={"field": "provider", "allowed": sorted(ALLOWED_PROVIDERS)},
        )
    out["provider"] = provider

    repository = str(out.get("repository") or "").strip()
    if not repository or not _REPO_RE.match(repository):
        raise ValidationError(
            "repository must be owner/name",
            kind="promotion_config_invalid",
            details={"field": "repository", "value": repository},
        )
    out["repository"] = repository

    api_base = str(out.get("api_base_url") or "").strip()
    if not api_base.startswith("http://") and not api_base.startswith("https://"):
        raise ValidationError(
            "api_base_url must be http(s) URL",
            kind="promotion_config_invalid",
            details={"field": "api_base_url"},
        )
    out["api_base_url"] = api_base.rstrip("/")

    integration = str(out.get("integration_branch") or "").strip()
    stable = str(out.get("stable_branch") or "").strip()
    if integration != "develop":
        raise ValidationError(
            "integration_branch must be 'develop' in v1.3",
            kind="promotion_config_invalid",
            details={"field": "integration_branch", "value": integration},
        )
    if stable != "master":
        raise ValidationError(
            "stable_branch must be 'master' in v1.3",
            kind="promotion_config_invalid",
            details={"field": "stable_branch", "value": stable},
        )
    out["integration_branch"] = integration
    out["stable_branch"] = stable

    merge_method = str(out.get("release_merge_method") or "").strip()
    if merge_method != "merge_commit":
        raise ValidationError(
            "release_merge_method must be 'merge_commit' in v1.3",
            kind="promotion_config_invalid",
            details={"field": "release_merge_method", "value": merge_method},
        )
    out["release_merge_method"] = merge_method

    freeze_dev = bool(out.get("freeze_develop_during_release", True))
    freeze_queue = bool(out.get("freeze_local_merge_queue_during_release", True))
    if not freeze_queue:
        raise ValidationError(
            "freeze_local_merge_queue_during_release must be true in v1.3",
            kind="promotion_config_invalid",
            details={"field": "freeze_local_merge_queue_during_release"},
        )
    if not freeze_dev:
        raise ValidationError(
            "freeze_develop_during_release must be true until alternate gate designed",
            kind="promotion_config_invalid",
            details={"field": "freeze_develop_during_release"},
        )
    out["freeze_develop_during_release"] = True
    out["freeze_local_merge_queue_during_release"] = True

    checks = out.get("required_checks")
    if not isinstance(checks, list) or not all(isinstance(c, str) and c for c in checks):
        raise ValidationError(
            "required_checks must be a non-empty list of strings",
            kind="promotion_config_invalid",
            details={"field": "required_checks"},
        )
    out["required_checks"] = list(checks)

    approvals = out.get("required_approvals", 1)
    if not isinstance(approvals, int) or isinstance(approvals, bool) or approvals < 1:
        raise ValidationError(
            "required_approvals must be int >= 1",
            kind="promotion_config_invalid",
            details={"field": "required_approvals"},
        )
    out["required_approvals"] = approvals

    # 丢弃未知键（已通过 secret 检查的也不得悄悄写入）
    allowed = set(default_promotion_entry())
    unknown = [k for k in raw if k not in allowed]
    if unknown:
        raise ValidationError(
            f"unknown promotion config keys: {', '.join(sorted(unknown))}",
            kind="promotion_config_invalid",
            details={"unknown_keys": unknown},
        )
    return out


def verify_remote_matches_repository(
    project: str,
    *,
    remote: str,
    repository: str,
) -> str:
    """校验 git remote get-url 与 repository identity 一致；返回 remote URL。"""
    root = get_project_path(project)
    bare = root / BARE_DIR_NAME
    if not bare.is_dir():
        raise OrchError(
            f".bare.git missing under {root}",
            code=ExitCode.GENERAL,
            kind="bare_missing",
            details={"path": str(root)},
        )
    result = run_git_ref(["remote", "get-url", remote], bare)
    if not result.ok:
        raise ValidationError(
            f"git remote {remote!r} not configured or unreachable",
            kind="promotion_config_invalid",
            details={
                "remote": remote,
                "stderr": (result.stderr or "").strip()[:400],
            },
        )
    url = result.stdout.strip()
    identity = parse_github_repository_identity(url)
    if identity is None:
        raise ValidationError(
            "cannot parse owner/repo from remote URL",
            kind="promotion_config_invalid",
            details={"remote": remote, "url_redacted_host_only": True},
        )
    if identity.lower() != repository.lower():
        raise ValidationError(
            "repository identity does not match git remote URL",
            kind="promotion_config_invalid",
            details={
                "configured_repository": repository,
                "remote_identity": identity,
                "remote": remote,
            },
        )
    return url


def write_promotion_config(project: str, entry: dict[str, Any]) -> dict[str, Any]:
    validated = validate_promotion_fields(entry)
    verify_remote_matches_repository(
        project,
        remote=validated["remote"],
        repository=validated["repository"],
    )
    cfg = read_config()
    promo = cfg.setdefault("promotion", {})
    if not isinstance(promo, dict):
        promo = {}
        cfg["promotion"] = promo
    promo[project] = validated
    write_config_atomic(cfg)
    return validated
