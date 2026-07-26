"""OpenCode runtime control plane (v1.2 Phase 0+).

Probe and adapter live here. Must not import host merge/queue writers for
read-only capability checks. No third-party dependencies.
"""

from __future__ import annotations

from orch.runtime.probe import run_capability_probe

__all__ = ["run_capability_probe"]
