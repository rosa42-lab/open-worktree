"""project list / add / remove."""

from __future__ import annotations

from typing import Any

from orch import registry


def cmd_project_list() -> dict[str, Any]:
    projects = registry.list_projects()
    return {"projects": projects}


def cmd_project_add(name: str, path: str) -> dict[str, Any]:
    return registry.add_project(name, path)


def cmd_project_remove(name: str) -> dict[str, Any]:
    return registry.remove_project(name)
