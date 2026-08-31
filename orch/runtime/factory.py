"""Build RuntimeAdapter instances. Callers must not import a concrete adapter."""

from __future__ import annotations

from orch.errors import ValidationError
from orch.runtime.adapter import CapabilityMatrix, RuntimeAdapter
from orch.runtime.binary import KNOWN_KINDS
from orch.runtime.http_client import OpenCodeHttpClient

CLASS_A_KINDS = frozenset({"opencode"})
CLASS_B_KINDS = frozenset({"claude"})


def class_for_kind(kind: str) -> str:
    """Map executor kind to Class A/B. Grok/Codex are known but not wired."""
    key = str(kind or "").strip().lower()
    if key in CLASS_A_KINDS:
        return "A"
    if key in CLASS_B_KINDS:
        return "B"
    if key in KNOWN_KINDS:
        raise ValidationError(
            f"executor kind {key} is not wired",
            kind="runtime_class_not_wired",
            details={"kind": key},
        )
    raise ValidationError(
        f"unknown executor kind: {kind}",
        kind="runtime_unknown_kind",
        details={"kind": kind},
    )


def adapter_from_client(
    client: OpenCodeHttpClient,
    *,
    known_capabilities: CapabilityMatrix | None = None,
) -> RuntimeAdapter:
    from orch.runtime.opencode import OpenCodeRuntimeAdapter

    return OpenCodeRuntimeAdapter(client, known_capabilities=known_capabilities)
