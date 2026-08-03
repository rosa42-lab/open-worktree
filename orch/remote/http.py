"""stdlib GitHub HTTP 客户端（V13-009-1）。Authorization 永不进日志/异常详情。"""

from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.request import Request

# 稳定错误 kind（合约测试断言这些字符串）
KIND_AUTH_FAILED = "auth_failed"
KIND_FORBIDDEN = "forbidden"
KIND_NOT_FOUND = "not_found"
KIND_VALIDATION = "validation"
KIND_RATE_LIMITED = "rate_limited"
KIND_SERVER_ERROR = "server_error"
KIND_TIMEOUT = "timeout"
KIND_NETWORK = "network"

_SECRET_RE = re.compile(
    r"(?i)(bearer\s+)[a-z0-9._\-]+|"
    r"(gh[pousr]_[a-z0-9]+)|"
    r"(ghs_[a-z0-9]+)|"
    r"(github_pat_[a-z0-9_]+)",
)


def redact_secrets(text: str) -> str:
    """从任意文本中抹去疑似 token / Bearer 值。"""
    if not text:
        return text
    out = _SECRET_RE.sub(lambda m: (m.group(1) or "") + "***", text)
    return out


def status_to_kind(status: int | None) -> str:
    if status is None:
        return KIND_NETWORK
    if status in (401,):
        return KIND_AUTH_FAILED
    if status in (403,):
        return KIND_FORBIDDEN
    if status in (404,):
        return KIND_NOT_FOUND
    if status in (422,):
        return KIND_VALIDATION
    if status in (429,):
        return KIND_RATE_LIMITED
    if 500 <= status <= 599:
        return KIND_SERVER_ERROR
    return KIND_NETWORK


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        if not self.body:
            return None
        return json.loads(self.text())


class ProviderHttpError(Exception):
    """远程 hosting HTTP 错误；message/details 已脱敏。"""

    def __init__(
        self,
        message: str,
        *,
        kind: str,
        status: int | None = None,
        path: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        safe = redact_secrets(message)
        super().__init__(safe)
        self.kind = kind
        self.status = status
        self.path = path
        self.details = _sanitize_details(details or {})

    def __repr__(self) -> str:
        return (
            f"ProviderHttpError(kind={self.kind!r}, status={self.status!r}, "
            f"path={self.path!r})"
        )


def _sanitize_details(details: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in details.items():
        key = str(k).lower()
        if key in ("authorization", "token", "access_token", "password", "secret"):
            out[k] = "***"
            continue
        if isinstance(v, str):
            out[k] = redact_secrets(v)[:800]
        elif isinstance(v, dict):
            out[k] = _sanitize_details(v)
        else:
            out[k] = v
    return out


class GitHubHttpClient:
    """Bearer token HTTP；token 仅存实例属性，不出现在错误字符串中。"""

    def __init__(
        self,
        token: str,
        *,
        api_base_url: str = "https://api.github.com",
        timeout_sec: float = 30.0,
        user_agent: str = "orch-remote/1.3",
        urlopen: Any = None,
    ) -> None:
        self._token = token
        self.api_base_url = api_base_url.rstrip("/")
        self.timeout_sec = timeout_sec
        self.user_agent = user_agent
        self._urlopen = urlopen or urllib.request.urlopen

    def __repr__(self) -> str:
        return f"GitHubHttpClient(api_base_url={self.api_base_url!r}, token=***)"

    def _url(self, path: str, query: dict[str, str] | None = None) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            url = path
        else:
            if not path.startswith("/"):
                path = "/" + path
            url = f"{self.api_base_url}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        return url

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        query: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        timeout_sec: float | None = None,
        accept: str = "application/vnd.github+json",
    ) -> HttpResponse:
        url = self._url(path, query)
        hdrs: dict[str, str] = {
            "Accept": accept,
            "User-Agent": self.user_agent,
            "X-GitHub-Api-Version": "2022-11-28",
            "Authorization": f"Bearer {self._token}",
        }
        if headers:
            hdrs.update(headers)
        data: bytes | None = None
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        req = Request(url, data=data, headers=hdrs, method=method.upper())
        timeout = self.timeout_sec if timeout_sec is None else timeout_sec
        try:
            with self._urlopen(req, timeout=timeout) as resp:
                body = resp.read()
                resp_headers = {k.lower(): v for k, v in resp.headers.items()}
                return HttpResponse(status=resp.status, headers=resp_headers, body=body)
        except urllib.error.HTTPError as exc:
            raw = b""
            try:
                raw = exc.read() if exc.fp is not None else b""
            except Exception:  # noqa: BLE001
                raw = b""
            body_text = redact_secrets(raw.decode("utf-8", errors="replace"))[:800]
            kind = status_to_kind(exc.code)
            # 429 有时以 403 + rate limit 头出现；保留 status 映射
            raise ProviderHttpError(
                f"HTTP {exc.code} {method.upper()} {path}",
                kind=kind,
                status=exc.code,
                path=path,
                details={"body": body_text},
            ) from None
        except urllib.error.URLError as exc:
            reason = redact_secrets(str(getattr(exc, "reason", exc)))
            kind = KIND_TIMEOUT if isinstance(getattr(exc, "reason", None), TimeoutError) else KIND_NETWORK
            if "timed out" in reason.lower():
                kind = KIND_TIMEOUT
            raise ProviderHttpError(
                f"connection failed {method.upper()} {path}: {reason}",
                kind=kind,
                path=path,
            ) from None
        except (TimeoutError, socket.timeout) as exc:
            raise ProviderHttpError(
                f"timeout {method.upper()} {path}",
                kind=KIND_TIMEOUT,
                path=path,
            ) from None

    def get_json(
        self,
        path: str,
        *,
        query: dict[str, str] | None = None,
        timeout_sec: float | None = None,
    ) -> Any:
        return self.request("GET", path, query=query, timeout_sec=timeout_sec).json()

    def post_json(
        self,
        path: str,
        *,
        json_body: Any = None,
        query: dict[str, str] | None = None,
        timeout_sec: float | None = None,
    ) -> Any:
        return self.request(
            "POST",
            path,
            json_body=json_body if json_body is not None else {},
            query=query,
            timeout_sec=timeout_sec,
        ).json()
