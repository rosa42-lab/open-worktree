"""stdlib HTTP client for OpenCode Server (Basic Auth + SSE)."""

from __future__ import annotations

import base64
import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse
from urllib.request import Request


class HttpError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        body: str | None = None,
        url: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.body = body
        self.url = url


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


class OpenCodeHttpClient:
    """Minimal urllib/http.client client. Never logs password or Authorization."""

    def __init__(
        self,
        base_url: str,
        *,
        username: str | None = None,
        password: str | None = None,
        timeout_sec: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username or "opencode"
        self.password = password
        self.timeout_sec = timeout_sec

    def _auth_header(self) -> str | None:
        if not self.password:
            return None
        token = base64.b64encode(
            f"{self.username}:{self.password}".encode("utf-8")
        ).decode("ascii")
        return f"Basic {token}"

    def _build_url(
        self,
        path: str,
        *,
        query: dict[str, str] | None = None,
        directory: str | None = None,
    ) -> str:
        q = dict(query or {})
        if directory is not None:
            q.setdefault("directory", directory)
        url = f"{self.base_url}{path}"
        if q:
            url = f"{url}?{urllib.parse.urlencode(q)}"
        return url

    def request(
        self,
        method: str,
        path: str,
        *,
        directory: str | None = None,
        query: dict[str, str] | None = None,
        json_body: Any = None,
        headers: dict[str, str] | None = None,
        timeout_sec: float | None = None,
        accept: str | None = None,
    ) -> HttpResponse:
        url = self._build_url(path, query=query, directory=directory)
        hdrs: dict[str, str] = {
            "Accept": accept or "application/json",
            "User-Agent": "orch-runtime-probe/1.2",
        }
        auth = self._auth_header()
        if auth:
            hdrs["Authorization"] = auth
        if directory is not None:
            hdrs["x-opencode-directory"] = directory
        if headers:
            hdrs.update(headers)
        data: bytes | None = None
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        req = Request(url, data=data, headers=hdrs, method=method.upper())
        try:
            with urllib.request.urlopen(
                req, timeout=timeout_sec if timeout_sec is not None else self.timeout_sec
            ) as resp:
                body = resp.read()
                resp_headers = {k.lower(): v for k, v in resp.headers.items()}
                return HttpResponse(status=resp.status, headers=resp_headers, body=body)
        except urllib.error.HTTPError as exc:
            body = exc.read() if exc.fp is not None else b""
            raise HttpError(
                f"HTTP {exc.code} {method.upper()} {path}",
                status=exc.code,
                body=body.decode("utf-8", errors="replace"),
                url=self._redact_url(url),
            ) from None
        except urllib.error.URLError as exc:
            raise HttpError(
                f"connection failed {method.upper()} {path}: {exc.reason}",
                url=self._redact_url(url),
            ) from None
        except TimeoutError as exc:
            raise HttpError(
                f"timeout {method.upper()} {path}",
                url=self._redact_url(url),
            ) from exc

    def get_json(
        self,
        path: str,
        *,
        directory: str | None = None,
        query: dict[str, str] | None = None,
        timeout_sec: float | None = None,
    ) -> Any:
        return self.request(
            "GET",
            path,
            directory=directory,
            query=query,
            timeout_sec=timeout_sec,
        ).json()

    def post_json(
        self,
        path: str,
        *,
        directory: str | None = None,
        json_body: Any = None,
        query: dict[str, str] | None = None,
        timeout_sec: float | None = None,
        expect_empty: bool = False,
    ) -> Any:
        resp = self.request(
            "POST",
            path,
            directory=directory,
            query=query,
            json_body=json_body if json_body is not None else {},
            timeout_sec=timeout_sec,
        )
        if expect_empty or resp.status == 204 or not resp.body:
            return None
        return resp.json()

    def iter_sse(
        self,
        path: str,
        *,
        directory: str | None = None,
        query: dict[str, str] | None = None,
        timeout_sec: float = 15.0,
        idle_sec: float = 3.0,
        max_events: int = 50,
        stop_when: Callable[[dict[str, Any]], bool] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Read SSE frames over a raw TCP socket.

        OpenCode uses chunked Transfer-Encoding. urllib/http.client chunked
        readers block until a full chunk or poison the stream after a socket
        timeout. A raw socket + manual header parse keeps heartbeats usable.
        """
        url = self._build_url(path, query=query, directory=directory)
        parsed = urlparse(url)
        if parsed.scheme != "http":
            raise HttpError(
                f"SSE probe currently supports http only (got {parsed.scheme})",
                url=self._redact_url(url),
            )
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80
        path_q = parsed.path or "/"
        if parsed.query:
            path_q = f"{path_q}?{parsed.query}"

        headers = [
            f"GET {path_q} HTTP/1.1",
            f"Host: {host}:{port}",
            "Accept: text/event-stream",
            "Cache-Control: no-cache",
            "Connection: close",
            "User-Agent: orch-runtime-probe/1.2",
        ]
        auth = self._auth_header()
        if auth:
            headers.append(f"Authorization: {auth}")
        if directory is not None:
            headers.append(f"x-opencode-directory: {directory}")
        request_bytes = ("\r\n".join(headers) + "\r\n\r\n").encode("utf-8")

        events: list[dict[str, Any]] = []
        sock: socket.socket | None = None
        try:
            sock = socket.create_connection((host, port), timeout=timeout_sec)
            sock.settimeout(1.0)
            sock.sendall(request_bytes)

            # Read HTTP headers
            header_buf = b""
            deadline = time.monotonic() + timeout_sec
            while b"\r\n\r\n" not in header_buf and time.monotonic() < deadline:
                try:
                    chunk = sock.recv(1)
                except (TimeoutError, socket.timeout):
                    continue
                if not chunk:
                    break
                header_buf += chunk
            if b"\r\n\r\n" not in header_buf:
                raise HttpError(
                    f"SSE header timeout GET {path}",
                    url=self._redact_url(url),
                )
            header_text, body_start = header_buf.split(b"\r\n\r\n", 1)
            status_line = header_text.split(b"\r\n", 1)[0].decode("ascii", errors="replace")
            parts = status_line.split(" ", 2)
            status = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 0
            if status >= 400 or status == 0:
                raise HttpError(
                    f"HTTP {status} GET {path} (SSE)",
                    status=status or None,
                    body=header_text.decode("utf-8", errors="replace")[:500],
                    url=self._redact_url(url),
                )

            # Decode chunked body as opaque byte stream (de-chunk manually).
            raw = body_start
            decoded = b""
            # We may receive either raw SSE (unlikely) or HTTP/1.1 chunked.
            # Detect Transfer-Encoding from headers.
            header_lower = header_text.lower()
            chunked = b"transfer-encoding: chunked" in header_lower

            def pull(n: int = 4096) -> bytes:
                nonlocal raw
                if raw:
                    out, raw = raw[:n], raw[n:]
                    return out
                try:
                    return sock.recv(n) if sock is not None else b""
                except (TimeoutError, socket.timeout):
                    return b""

            def read_exact(n: int) -> bytes:
                buf = b""
                local_deadline = time.monotonic() + timeout_sec
                while len(buf) < n and time.monotonic() < local_deadline:
                    piece = pull(n - len(buf))
                    if piece:
                        buf += piece
                    else:
                        time.sleep(0.01)
                return buf

            last_data_at = time.monotonic()
            text_buf = ""
            while time.monotonic() < deadline and len(events) < max_events:
                if events and time.monotonic() - last_data_at > idle_sec:
                    break
                if chunked:
                    # Read chunk size line
                    size_line = b""
                    while b"\r\n" not in size_line:
                        if time.monotonic() >= deadline:
                            break
                        if events and time.monotonic() - last_data_at > idle_sec:
                            break
                        piece = pull(1)
                        if not piece:
                            if events and time.monotonic() - last_data_at > idle_sec:
                                break
                            continue
                        size_line += piece
                        last_data_at = time.monotonic()
                    if b"\r\n" not in size_line:
                        if events:
                            break
                        continue
                    size_str = size_line.split(b";", 1)[0].strip()
                    try:
                        size = int(size_str, 16)
                    except ValueError:
                        raise HttpError(
                            f"invalid chunk size in SSE GET {path}",
                            url=self._redact_url(url),
                        ) from None
                    if size == 0:
                        break
                    data = read_exact(size)
                    # trailing CRLF
                    _ = read_exact(2)
                    if not data:
                        continue
                    decoded = data
                else:
                    decoded = pull(256)
                    if not decoded:
                        if events and time.monotonic() - last_data_at > idle_sec:
                            break
                        continue
                    last_data_at = time.monotonic()

                text_buf += decoded.decode("utf-8", errors="replace")
                last_data_at = time.monotonic()
                while "\n\n" in text_buf or "\r\n\r\n" in text_buf:
                    if "\r\n\r\n" in text_buf:
                        frame, text_buf = text_buf.split("\r\n\r\n", 1)
                    else:
                        frame, text_buf = text_buf.split("\n\n", 1)
                    parsed_ev = _parse_sse_frame(frame)
                    if parsed_ev is None:
                        continue
                    events.append(parsed_ev)
                    if stop_when is not None and stop_when(parsed_ev):
                        return events
        except HttpError:
            raise
        except OSError as exc:
            raise HttpError(
                f"SSE connection failed GET {path}: {exc}",
                url=self._redact_url(url),
            ) from None
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        return events

    @staticmethod
    def _redact_url(url: str) -> str:
        parsed = urllib.parse.urlparse(url)
        if parsed.username or parsed.password:
            netloc = parsed.hostname or ""
            if parsed.port:
                netloc = f"{netloc}:{parsed.port}"
            parsed = parsed._replace(netloc=netloc)
        return urllib.parse.urlunparse(parsed)


def _parse_sse_frame(frame: str) -> dict[str, Any] | None:
    event_name = "message"
    data_lines: list[str] = []
    for line in frame.splitlines():
        if not line or line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if not data_lines:
        return None
    raw = "\n".join(data_lines)
    payload: Any
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = raw
    return {"event": event_name, "data": payload}
