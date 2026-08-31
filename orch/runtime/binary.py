"""Resolve executor binaries by kind + absolute path. Never spawn PATH name `agent` as generic."""

from __future__ import annotations

import os
from pathlib import Path

from orch.errors import ValidationError

KNOWN_KINDS = frozenset({"opencode", "claude", "grok", "codex"})
ENV_ALLOWLIST = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CONFIG_DIR",
        "CLAUDE_CODE_SIMPLE",
        "HOME",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "LANG",
        "LC_ALL",
        "TMP",
        "TEMP",
        "TMPDIR",
        "SystemRoot",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
    }
)
_AGENT_NAMES = frozenset({"agent", "agent.exe"})


def resolve_executor(*, kind: str, path: str) -> Path:
    kind_key = str(kind or "").strip().lower()
    if kind_key not in KNOWN_KINDS:
        raise ValidationError(
            f"unknown executor kind: {kind}",
            kind="runtime_unknown_kind",
            details={"kind": kind},
        )
    raw = str(path or "").strip()
    candidate = Path(raw)
    if not candidate.is_absolute():
        raise ValidationError(
            "executor path must be an absolute file path",
            kind="runtime_binary_not_absolute",
            details={"path": raw},
        )
    if not candidate.is_file():
        raise ValidationError(
            f"executor binary not found: {candidate}",
            kind="runtime_binary_missing",
            details={"path": str(candidate)},
        )
    name = candidate.name.lower()
    if name in _AGENT_NAMES and kind_key != "grok":
        raise ValidationError(
            "binary named agent is not a generic runtime; this host maps agent to grok",
            kind="runtime_binary_identity",
            details={"path": str(candidate), "kind": kind_key},
        )
    return candidate.resolve()


def spawn_env(*, binary: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    env: dict[str, str] = {}
    for key in ENV_ALLOWLIST:
        val = os.environ.get(key)
        if val:
            env[key] = val
    if extra:
        for key, val in extra.items():
            if key in ENV_ALLOWLIST and val is not None:
                env[key] = val
    env["PATH"] = str(binary.parent)
    env["NO_COLOR"] = "1"
    return env
