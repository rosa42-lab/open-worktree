"""Error types and exit-code mapping (task T-0101)."""

from __future__ import annotations

from typing import Any


class ExitCode:
    SUCCESS = 0
    GENERAL = 1
    USAGE = 2
    UNREGISTERED = 3
    PRECHECK = 4
    QUEUE_BLOCKED = 5
    LOCK = 6
    VALIDATION = 7  # enqueue / retry
    GIT = 8
    DB = 9
    INTERRUPTED = 130


ALL_EXIT_CODES = frozenset(
    {
        ExitCode.SUCCESS,
        ExitCode.GENERAL,
        ExitCode.USAGE,
        ExitCode.UNREGISTERED,
        ExitCode.PRECHECK,
        ExitCode.QUEUE_BLOCKED,
        ExitCode.LOCK,
        ExitCode.VALIDATION,
        ExitCode.GIT,
        ExitCode.DB,
        ExitCode.INTERRUPTED,
    }
)


class OrchError(Exception):
    """Base application error with stable kind and exit code."""

    def __init__(
        self,
        message: str,
        *,
        code: int = ExitCode.GENERAL,
        kind: str = "general_failure",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.kind = kind
        self.details = details or {}

    def to_error_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "kind": self.kind,
            "message": self.message,
            "details": self.details,
        }


class UsageError(OrchError):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message, code=ExitCode.USAGE, kind="usage_error", details=details
        )


class UnregisteredProjectError(OrchError):
    def __init__(self, project: str) -> None:
        super().__init__(
            f"project '{project}' is not registered; run: orch project add {project} <path>",
            code=ExitCode.UNREGISTERED,
            kind="unregistered_project",
            details={"project": project},
        )


class PrecheckError(OrchError):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message,
            code=ExitCode.PRECHECK,
            kind="merge_precheck_failed",
            details=details,
        )


class QueueBlockedError(OrchError):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message,
            code=ExitCode.QUEUE_BLOCKED,
            kind="queue_blocked",
            details=details,
        )


class LockError(OrchError):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message, code=ExitCode.LOCK, kind="lock_error", details=details
        )


class ValidationError(OrchError):
    def __init__(
        self,
        message: str,
        *,
        kind: str = "enqueue_validation_failed",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message, code=ExitCode.VALIDATION, kind=kind, details=details
        )


class GitError(OrchError):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message, code=ExitCode.GIT, kind="git_failure", details=details
        )


class DbError(OrchError):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message, code=ExitCode.DB, kind="database_error", details=details
        )


class InterruptedMergeError(OrchError):
    def __init__(self, message: str = "merge interrupted", details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message,
            code=ExitCode.INTERRUPTED,
            kind="merge_interrupted",
            details=details,
        )


class NotImplementedCommandError(OrchError):
    def __init__(self, command: str) -> None:
        super().__init__(
            f"command not implemented yet: {command}",
            code=ExitCode.GENERAL,
            kind="not_implemented",
            details={"command": command},
        )
