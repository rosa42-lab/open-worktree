"""Atomic config.json updates with config lock (task T-0109)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from orch.constants import config_lock_path, config_path, orchestrator_home
from orch.errors import OrchError, ExitCode
from orch.locks import acquire, release


def _default_config() -> dict[str, Any]:
    return {"projects": {}}


def read_config() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        return _default_config()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OrchError(
            f"cannot read config: {exc}",
            code=ExitCode.GENERAL,
            kind="config_error",
            details={"error": str(exc)},
        ) from exc
    if "projects" not in data or not isinstance(data["projects"], dict):
        data["projects"] = {}
    return data


def write_config_atomic(data: dict[str, Any]) -> None:
    home = orchestrator_home()
    home.mkdir(parents=True, exist_ok=True)
    path = config_path()
    tmp = path.with_suffix(".json.tmp")
    handle = acquire(config_lock_path(), command="config_write", project=None)
    try:
        text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        tmp.write_text(text, encoding="utf-8")
        os.replace(str(tmp), str(path))
    finally:
        release(handle)
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
