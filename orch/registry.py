"""Project registry queries (task T-0109)."""

from __future__ import annotations

from pathlib import Path

from orch.config import read_config, write_config_atomic
from orch.constants import BARE_DIR_NAME
from orch.errors import UnregisteredProjectError, UsageError, OrchError, ExitCode
from orch.validate import normalize_path, validate_project_name


def list_projects() -> dict[str, str]:
    cfg = read_config()
    return dict(cfg.get("projects") or {})


def get_project_path(name: str) -> Path:
    name = validate_project_name(name)
    projects = list_projects()
    if name not in projects:
        raise UnregisteredProjectError(name)
    return Path(projects[name])


def add_project(name: str, path_str: str) -> dict[str, str]:
    name = validate_project_name(name)
    root = normalize_path(path_str, label="path")
    bare = root / BARE_DIR_NAME
    if not bare.exists():
        raise OrchError(
            f".bare.git missing under {root}; create bare repo before project add",
            code=ExitCode.GENERAL,
            kind="bare_missing",
            details={"path": str(root), "bare": str(bare)},
        )
    cfg = read_config()
    projects = cfg.setdefault("projects", {})
    if name in projects:
        raise UsageError(
            f"project name already registered: {name}",
            details={"name": name, "existing_path": projects[name]},
        )
    # Also reject same path re-registered under different name? design only says unique name.
    projects[name] = str(root)
    write_config_atomic(cfg)
    return {"name": name, "path": str(root)}


def remove_project(name: str) -> dict[str, str]:
    name = validate_project_name(name)
    from orch.constants import project_lock_path

    lock = project_lock_path(name)
    if lock.exists():
        raise OrchError(
            f"project '{name}' still has project.lock; refuse remove",
            code=ExitCode.LOCK,
            kind="lock_error",
            details={"lock": str(lock)},
        )
    cfg = read_config()
    projects = cfg.setdefault("projects", {})
    if name not in projects:
        raise UnregisteredProjectError(name)
    path = projects.pop(name)
    write_config_atomic(cfg)
    return {"name": name, "path": path, "data_kept": True}
