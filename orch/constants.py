"""Global constants for orch v1.1. TARGET_BRANCH is hard-coded."""

from __future__ import annotations

from pathlib import Path

TARGET_BRANCH = "develop"
BARE_DIR_NAME = ".bare.git"
MAIN_WORKTREE_NAME = "main"
WORKTREES_DIR_NAME = "worktrees"

JSON_SCHEMA_VERSION = 1
LOCK_WAIT_TIMEOUT_SEC = 30.0
GIT_TIMEOUT_SEC = 60.0
DB_BUSY_TIMEOUT_MS = 5000
CLEANUP_COOLDOWN_HOURS = 24

ORCHESTRATOR_HOME_NAME = ".orchestrator"
CONFIG_FILE_NAME = "config.json"
CONFIG_LOCK_NAME = "config.json.lock"
PROJECT_DB_NAME = "orchestrator.db"
PROJECT_LOCK_NAME = "project.lock"

# Host-level OpenCode runtime registry (v1.2)
RUNTIME_DIR_NAME = "runtime"
RUNTIME_REGISTRY_NAME = "opencode.json"
RUNTIME_CREDENTIALS_NAME = "opencode.credentials.json"
RUNTIME_LOCK_NAME = "opencode.lock"
RUNTIME_LOG_DIR_NAME = "logs"
RUNTIME_LOG_NAME = "opencode.log"
DEFAULT_RUNTIME_PORT = 4096
DEFAULT_RUNTIME_HOST = "127.0.0.1"
DEFAULT_RUNTIME_USERNAME = "opencode"


def orchestrator_home() -> Path:
    return Path.home() / ORCHESTRATOR_HOME_NAME


def config_path() -> Path:
    return orchestrator_home() / CONFIG_FILE_NAME


def config_lock_path() -> Path:
    return orchestrator_home() / CONFIG_LOCK_NAME


def project_data_dir(project: str) -> Path:
    return orchestrator_home() / "data" / project


def project_db_path(project: str) -> Path:
    return project_data_dir(project) / PROJECT_DB_NAME


def project_lock_path(project: str) -> Path:
    return project_data_dir(project) / PROJECT_LOCK_NAME


def runtime_dir() -> Path:
    return orchestrator_home() / RUNTIME_DIR_NAME


def runtime_registry_path() -> Path:
    return runtime_dir() / RUNTIME_REGISTRY_NAME


def runtime_credentials_path() -> Path:
    return runtime_dir() / RUNTIME_CREDENTIALS_NAME


def runtime_lock_path() -> Path:
    return runtime_dir() / RUNTIME_LOCK_NAME


def runtime_log_dir() -> Path:
    return runtime_dir() / RUNTIME_LOG_DIR_NAME


def runtime_log_path() -> Path:
    return runtime_log_dir() / RUNTIME_LOG_NAME


def skill_install_path() -> Path:
    return orchestrator_home() / "skills" / "orchestrator" / "SKILL.md"
