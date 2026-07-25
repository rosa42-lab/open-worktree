"""JSON envelope helpers (task T-0102)."""

from __future__ import annotations

import json
import sys
from typing import Any

from orch.constants import JSON_SCHEMA_VERSION
from orch.errors import OrchError


def success_envelope(command: str, data: Any = None) -> dict[str, Any]:
    return {
        "schema_version": JSON_SCHEMA_VERSION,
        "ok": True,
        "command": command,
        "data": data if data is not None else {},
        "error": None,
    }


def error_envelope(
    command: str,
    *,
    code: int,
    kind: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": JSON_SCHEMA_VERSION,
        "ok": False,
        "command": command,
        "data": None,
        "error": {
            "code": code,
            "kind": kind,
            "message": message,
            "details": details or {},
        },
    }


def envelope_from_exception(command: str, exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, OrchError):
        return error_envelope(
            command,
            code=exc.code,
            kind=exc.kind,
            message=exc.message,
            details=exc.details,
        )
    return error_envelope(
        command,
        code=1,
        kind="general_failure",
        message=str(exc) or exc.__class__.__name__,
        details={"type": type(exc).__name__},
    )


def dumps(obj: dict[str, Any]) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False)


def emit_json(obj: dict[str, Any], *, stream=None) -> None:
    stream = stream or sys.stdout
    stream.write(dumps(obj))
    stream.write("\n")
