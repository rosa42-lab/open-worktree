"""GitHub 凭证解析（V13-009-1）。token/PEM 不进 config / argv / 日志。"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orch.remote.http import GitHubHttpClient, ProviderHttpError, redact_secrets


@dataclass(frozen=True)
class GitHubCredentials:
    """内存中的 bearer；可选角色标签。"""

    token: str
    role: str  # "default" | "integration" | "release"
    source: str  # "env" | "app_installation"


@dataclass(frozen=True)
class ResolvedGitHubAuth:
    default: GitHubCredentials | None
    integration: GitHubCredentials | None
    release: GitHubCredentials | None

    def best_for_probe(self) -> GitHubCredentials | None:
        return self.release or self.default or self.integration

    def for_develop_policy(self) -> GitHubCredentials | None:
        return self.integration or self.default

    def for_pr_and_checks(self) -> GitHubCredentials | None:
        return self.release or self.default


def _env(name: str) -> str | None:
    val = os.environ.get(name, "").strip()
    return val or None


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _find_openssl() -> str | None:
    explicit = _env("ORCH_OPENSSL_PATH")
    if explicit and Path(explicit).is_file():
        return explicit
    return shutil.which("openssl")


def mint_installation_token(
    *,
    app_id: str,
    installation_id: str,
    pem_path: str,
    api_base_url: str = "https://api.github.com",
    openssl_bin: str | None = None,
    http_client_factory: Any = None,
) -> str:
    """
    用 App JWT 换取短期 installation token（仅内存）。
    依赖本机 openssl 做 RS256；不引入第三方 crypto。
    """
    pem = Path(pem_path)
    if not pem.is_file():
        raise ProviderHttpError(
            "GitHub App PEM not found",
            kind="misconfigured",
            details={"field": "pem"},
        )
    openssl = openssl_bin or _find_openssl()
    if not openssl:
        raise ProviderHttpError(
            "openssl not found for App JWT signing",
            kind="misconfigured",
            details={"hint": "set ORCH_OPENSSL_PATH or install openssl"},
        )

    now = int(time.time())
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64url(
        json.dumps(
            {"iat": now - 60, "exp": now + 9 * 60, "iss": str(app_id)},
            separators=(",", ":"),
        ).encode()
    )
    signing_input = f"{header}.{payload}".encode()
    try:
        sig = subprocess.check_output(
            [openssl, "dgst", "-sha256", "-sign", str(pem)],
            input=signing_input,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ProviderHttpError(
            "failed to sign GitHub App JWT",
            kind="misconfigured",
            details={"error": type(exc).__name__},
        ) from None
    jwt = f"{header}.{payload}.{_b64url(sig)}"

    # 用 JWT 换 installation token；临时 client 不复用业务 token
    factory = http_client_factory or (
        lambda tok: GitHubHttpClient(tok, api_base_url=api_base_url, timeout_sec=30.0)
    )
    client = factory(jwt)
    try:
        data = client.post_json(f"/app/installations/{installation_id}/access_tokens", json_body={})
    except ProviderHttpError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ProviderHttpError(
            "installation token request failed",
            kind="network",
            details={"error": type(exc).__name__},
        ) from None
    if not isinstance(data, dict) or not data.get("token"):
        raise ProviderHttpError(
            "installation token response missing token",
            kind="validation",
        )
    return str(data["token"])


def _creds_from_bearer(token: str, role: str) -> GitHubCredentials:
    return GitHubCredentials(token=token, role=role, source="env")


def _creds_from_app(
    *,
    prefix: str,
    role: str,
    api_base_url: str,
) -> GitHubCredentials | None:
    token = _env(f"{prefix}_TOKEN") if prefix else None
    # 通用前缀 ORCH_GITHUB 已在外层处理
    app_id = _env(f"{prefix}_APP_ID")
    inst = _env(f"{prefix}_INSTALLATION_ID")
    pem = _env(f"{prefix}_APP_PEM")
    if token:
        return _creds_from_bearer(token, role)
    if app_id and inst and pem:
        minted = mint_installation_token(
            app_id=app_id,
            installation_id=inst,
            pem_path=pem,
            api_base_url=api_base_url,
        )
        return GitHubCredentials(token=minted, role=role, source="app_installation")
    return None


def resolve_github_auth(*, api_base_url: str = "https://api.github.com") -> ResolvedGitHubAuth:
    """
    解析顺序（plan）：
    1. ORCH_GITHUB_TOKEN
    2. ORCH_GITHUB_APP_ID + INSTALLATION_ID + APP_PEM
    3. 可选双 App：ORCH_GITHUB_INTEGRATION_* / ORCH_GITHUB_RELEASE_*
    """
    default: GitHubCredentials | None = None
    bearer = _env("ORCH_GITHUB_TOKEN")
    if bearer:
        default = _creds_from_bearer(bearer, "default")
    else:
        app_id = _env("ORCH_GITHUB_APP_ID")
        inst = _env("ORCH_GITHUB_INSTALLATION_ID")
        pem = _env("ORCH_GITHUB_APP_PEM")
        if app_id and inst and pem:
            try:
                minted = mint_installation_token(
                    app_id=app_id,
                    installation_id=inst,
                    pem_path=pem,
                    api_base_url=api_base_url,
                )
                default = GitHubCredentials(
                    token=minted, role="default", source="app_installation"
                )
            except ProviderHttpError:
                default = None

    integration = _creds_from_app(
        prefix="ORCH_GITHUB_INTEGRATION",
        role="integration",
        api_base_url=api_base_url,
    )
    release = _creds_from_app(
        prefix="ORCH_GITHUB_RELEASE",
        role="release",
        api_base_url=api_base_url,
    )
    return ResolvedGitHubAuth(default=default, integration=integration, release=release)


def auth_summary_for_probe(auth: ResolvedGitHubAuth) -> dict[str, Any]:
    """供 probe evidence：不含 token。"""
    def _one(c: GitHubCredentials | None) -> dict[str, Any] | None:
        if c is None:
            return None
        return {"role": c.role, "source": c.source, "present": True}

    return {
        "default": _one(auth.default),
        "integration": _one(auth.integration),
        "release": _one(auth.release),
        "note": redact_secrets("tokens redacted"),
    }
